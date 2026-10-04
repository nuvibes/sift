# SPDX-License-Identifier: AGPL-3.0-or-later
"""A switch on a settings screen changes something.

A control that is drawn, stores its value and redraws correctly, while no code reads the value,
looks
like it works and is a fact about the whole tree no reviewer holds. So every registered setting must
be read, by its key string, on the side its `read_by` names (`server` or `client`): every screen
names the settings it renders, so "the key appears in the client" would pass them all. A read that
only REPORTS the value counts only if a screen reads that wire field (`landing`). A registration in
a shape this cannot follow is reported, not skipped. It proves something reads a value, not that it
does the right thing with it.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import textwrap
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from sift.kernel.settings_registry import ReadBy, registered_settings
from sift.kernel.wire import Wire
from sift.main import create_app
from tests.gates import client_source

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "src" / "sift"
CLIENT = REPO / "frontend" / "src"

#: Settings nothing reads, and why each is not a fault.
#:
#: A reason, not a name. If the sentence is hard to write, the honest answer is that somebody added
#: a control and never wired up the thing it was meant to change.
CHANGES_NOTHING_ON_PURPOSE: dict[str, str] = {}


#: What a namespace lookup found nothing for. Distinct from None, which a module can hold.
_MISSING = object()

#: Wrappers that turn a display into a container without changing what it holds.
_WRAPPERS = frozenset({"frozenset", "tuple", "set", "list", "dict", "MappingProxyType"})

Namespace = Mapping[str, object]


def _resolve(node: ast.expr, namespace: Namespace) -> object:
    """What an expression names, looked up in the LIVE module it is written in: by value, not
    spelling, so two slices' `ENABLED_KEY` are two keys. Only through modules and classes; any other
    attribute is computed at run time."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return namespace.get(node.id, _MISSING)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, namespace)
        if isinstance(base, ModuleType):
            return vars(base).get(node.attr, _MISSING)
        if isinstance(base, type):
            return inspect.getattr_static(base, node.attr, _MISSING)
    return _MISSING


def _strings(value: object) -> set[str]:
    """The setting keys a resolved value could be: itself, or what a container holds."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {one for one in (*value.keys(), *value.values()) if isinstance(one, str)}
    if isinstance(value, tuple | list | set | frozenset):
        return {one for one in value if isinstance(one, str)}
    return set()


def _is_declaration(node: ast.expr) -> bool:
    """A value that only WRITES DOWN what it holds: a name, a literal, a display, or one wrapped.

    Assigning one of these at module or class level declares something; it reads nothing. A call to
    anything else (a watcher built from a list of keys, say) is doing work at import time, and
    the keys it is handed are being used.
    """
    if isinstance(node, ast.Constant | ast.Name | ast.Attribute):
        return True
    if isinstance(node, ast.Dict | ast.Tuple | ast.List | ast.Set):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _WRAPPERS
        and all(_is_declaration(one) for one in node.args)
    )


def _declared_values(tree: ast.Module) -> Iterator[tuple[ast.Assign | ast.AnnAssign, ast.expr]]:
    """Every declaration at module or class level, as the statement and the value it assigns."""
    bodies: list[list[ast.stmt]] = [tree.body]
    while bodies:
        for statement in bodies.pop():
            if isinstance(statement, ast.ClassDef):
                bodies.append(statement.body)
            elif (
                isinstance(statement, ast.Assign | ast.AnnAssign)
                and statement.value is not None
                and _is_declaration(statement.value)
            ):
                yield statement, statement.value


def _is_registration(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "register_setting")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "register_setting")
    )


def _within(roots: Iterable[ast.AST]) -> set[int]:
    return {id(inner) for root in roots for inner in ast.walk(root)}


def declared_containers(tree: ast.Module, namespace: Namespace) -> dict[int, set[str]]:
    """Each container a module declares, by the identity of the live object, with what it names.

    What it names comes from the SOURCE of the display rather than from the object, deliberately.
    The registry itself is a module-level `{}` that every registration fills: read from the object,
    one use of it would count every setting there is as read, which is the same hole as counting a
    declaration and a much wider one.
    """
    found: dict[int, set[str]] = {}
    for statement, value in _declared_values(tree):
        if not isinstance(value, ast.Dict | ast.Tuple | ast.List | ast.Set | ast.Call):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        named: set[str] = set()
        for inner in ast.walk(value):
            if isinstance(inner, ast.Constant | ast.Name | ast.Attribute):
                named |= _strings(_resolve(inner, namespace))
        for target in targets:
            if isinstance(target, ast.Name) and (live := namespace.get(target.id)) is not None:
                found[id(live)] = named
    return found


def reads(
    source: str,
    namespace: Namespace,
    *,
    wanted: set[str],
    makers: Iterable[object] = (),
    containers: Mapping[int, set[str]] | None = None,
) -> tuple[set[str], set[int]]:
    """Which of `wanted` this module reads, and which key-making functions it calls (by identity).

    A read is any expression, anywhere, that resolves to the key (a constant, the literal string,
    a container that was declared holding it) EXCEPT inside a `register_setting` call and inside a
    declaration. Those two exclusions are the gate: registering a setting and writing its key down
    are what every setting does, so counting either would make them all look alive.

    Deliberately broad otherwise. What is asked is whether ANY code touches the key: passing it to
    a reader, mapping a job type to it, comparing against it, looking it up. Narrowing it to "passed
    to `get_app`" would report the Performance switches as dead, because they are reached through a
    map from job type to setting rather than by being named at a call.

    `containers` is every module's declared containers, because a map is often declared in one
    module and read in another; without it, this module's own.
    """
    tree = ast.parse(source)
    read: set[str] = set()
    called: set[int] = set()
    for _node, keys, maker in _read_sites(tree, namespace, wanted, makers, containers):
        read |= keys
        if maker is not None:
            called.add(maker)
    return read, called


def _read_sites(
    tree: ast.Module,
    namespace: Namespace,
    wanted: set[str],
    makers: Iterable[object] = (),
    containers: Mapping[int, set[str]] | None = None,
) -> Iterator[tuple[ast.AST, set[str], int | None]]:
    """Every node that reads a wanted key, with the keys it reads, or calls a key-making function,
    with that function's identity. The walk `reads` answers from, kept as sites so a caller can ask
    where each read's value goes (see `landing`)."""
    skipped = _within(node for node in ast.walk(tree) if _is_registration(node))
    skipped |= _within(value for _statement, value in _declared_values(tree))
    if containers is None:
        containers = declared_containers(tree, namespace)
    maker_ids = {id(one) for one in makers}

    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Call) and id(target := _resolve(node.func, namespace)) in maker_ids:
            yield node, set(), id(target)
        if not isinstance(node, ast.Constant | ast.Name | ast.Attribute):
            continue
        if isinstance(node, ast.Name | ast.Attribute) and not isinstance(node.ctx, ast.Load):
            continue
        value = _resolve(node, namespace)
        keys = containers.get(id(value), set()) & wanted
        if isinstance(value, str) and value in wanted:
            keys = keys | {value}
        if keys:
            yield node, keys, None


#: How many hops `landing` follows (a helper into a builder into a model) before it stops and
#: counts the read as acted on. The real shape is two; a chain longer than four is something this
#: cannot follow honestly, and counting it as a use is the answer that never reports a live setting.
_HOPS = 4


def _wire_fields(value: object) -> frozenset[str]:
    """The fields of a wire model, when `value` is one; nothing otherwise."""
    if isinstance(value, type) and issubclass(value, Wire):
        return frozenset(value.model_fields)
    return frozenset()


def _enclosing_function(
    node: ast.AST, parents: Mapping[int, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    above = parents.get(id(node))
    while above is not None:
        if isinstance(above, ast.FunctionDef | ast.AsyncFunctionDef):
            return above
        above = parents.get(id(above))
    return None


def landing(
    node: ast.AST,
    tree: ast.Module,
    namespace: Namespace,
    parents: Mapping[int, ast.AST] | None = None,
    hops: int = 0,
) -> frozenset[str] | None:
    """The wire fields a read's value goes into, when that is ALL it does, or None, when the
    server acts on it (or this cannot tell, which is counted the same way on purpose).

    Two shapes are followed, because they are the two a value takes on its way into an answer:

    - **straight into a keyword** of a call: a wire model's field (`SelfTestView(offer=...)`) is
      where it lands; a function of this module is followed through the parameter of that name to
      wherever the function puts it (`_view(offer=...)` and then `SelfTestView(offer=offer)`).
    - **inside a private helper** (`_should_offer`) whose every call in this module is itself one
      of those: whatever the helper does with the key, its only output is its answer, so the read
      lands wherever the answer does.

    Anything else (a comparison deciding what a route does, a value handed to another feature,
    a public function others may call) is the server acting on the value.
    """
    if hops > _HOPS:
        return None
    if parents is None:
        parents = {
            id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
    current: ast.AST = node
    while (above := parents.get(id(current))) is not None:
        if isinstance(above, ast.Lambda):
            return None
        if isinstance(above, ast.keyword) and above.arg is not None:
            call = parents.get(id(above))
            if not isinstance(call, ast.Call):
                return None
            return _through_keyword(call, above.arg, tree, namespace, parents, hops)
        if isinstance(above, ast.stmt):
            break
        current = above
    helper = _enclosing_function(node, parents)
    if helper is None or not helper.name.startswith("_") or helper not in tree.body:
        return None
    calls = [
        one
        for one in ast.walk(tree)
        if isinstance(one, ast.Call)
        and isinstance(one.func, ast.Name)
        and one.func.id == helper.name
    ]
    if not calls:
        return None
    found: set[str] = set()
    for call in calls:
        lands = landing(call, tree, namespace, parents, hops + 1)
        if lands is None:
            return None
        found |= lands
    return frozenset(found)


def _through_keyword(
    call: ast.Call,
    name: str,
    tree: ast.Module,
    namespace: Namespace,
    parents: Mapping[int, ast.AST],
    hops: int,
) -> frozenset[str] | None:
    """Where a value handed to `call` as `name=` ends up. See `landing`."""
    fields = _wire_fields(_resolve(call.func, namespace))
    if name in fields:
        return frozenset({name})
    if fields:
        return None
    if not isinstance(call.func, ast.Name):
        return None
    builder = next(
        (
            one
            for one in tree.body
            if isinstance(one, ast.FunctionDef | ast.AsyncFunctionDef) and one.name == call.func.id
        ),
        None,
    )
    if builder is None:
        return None
    arguments = builder.args
    if name not in {one.arg for one in (*arguments.args, *arguments.kwonlyargs)}:
        return None
    found: set[str] = set()
    for use in ast.walk(builder):
        if isinstance(use, ast.Name) and use.id == name and isinstance(use.ctx, ast.Load):
            lands = landing(use, tree, namespace, parents, hops + 1)
            if lands is None:
                return None
            found |= lands
    return frozenset(found)


def client_reads_field(field: str, client: str) -> bool:
    """Whether the browser client reads a field of an answer: `selfTest.offer`, `x?.offer`,
    `x['offer']`. Written so a CSS class of the same name (`.offer {`) is not a read."""
    escaped = re.escape(field)
    return re.search(rf"(?<=[\w)\]])\??\.{escaped}\b|\[['\"]{escaped}['\"]\]", client) is not None


def maker_pattern(function: object) -> re.Pattern[str] | None:
    """The keys a key-making function can return, when every return is an f-string with a head.

    `strategy_key` is `f"enrich.{subject.value}.{key}"`: forty settings are registered through it
    and read through it, and none has a constant of its own, so a gate reading constants alone
    would see none of them. A maker's settings are read when the maker is CALLED somewhere other
    than a registration; the pattern says which settings are its, so calling one maker does not
    vouch for another's.

    The fixed head is required. A pattern that began with a hole would match any key at all.
    """
    try:
        found = ast.parse(textwrap.dedent(inspect.getsource(function)))  # type: ignore[arg-type]
    except (OSError, TypeError, SyntaxError):
        return None
    shapes: list[str] = []
    for node in ast.walk(found):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if not isinstance(value, ast.JoinedStr) or not value.values:
            return None
        head = value.values[0]
        if not (isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value):
            return None
        shapes.append(
            "".join(
                re.escape(part.value) if isinstance(part, ast.Constant) else ".+"
                for part in value.values
            )
        )
    return re.compile("|".join(f"(?:{one})" for one in shapes)) if shapes else None


def _loop_items(
    call: ast.Call, name: str, parents: Mapping[int, ast.AST], namespace: Namespace
) -> object:
    """What the `for` loop around a registration walks, when the loop's variable is `name`."""
    loop = parents.get(id(call))
    while loop is not None:
        if (
            isinstance(loop, ast.For)
            and isinstance(loop.target, ast.Name)
            and loop.target.id == name
        ):
            return _resolve(loop.iter, namespace)
        loop = parents.get(id(loop))
    return _MISSING


def located(source: str, namespace: Namespace) -> tuple[set[str], list[object]]:
    """The keys this module's registrations name, and the key-making functions they call.

    Four shapes are in the tree and all four are read: a constant or a literal; one entry of a
    declared map (`KEY_FOR_PRESET[preset]`); a field of each item a loop walks (`_mark.key`, the
    tile marks); and a call to a function that builds the key. Anything else is reported by the test
    below as a setting this gate cannot find, rather than passed over.
    """
    tree = ast.parse(source)
    parents = {
        id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    keys: set[str] = set()
    makers: list[object] = []
    for call in (node for node in ast.walk(tree) if _is_registration(node)):
        assert isinstance(call, ast.Call)
        expression = next((one.value for one in call.keywords if one.arg == "key"), None)
        if expression is None:
            continue
        if isinstance(expression, ast.Call):
            if callable(maker := _resolve(expression.func, namespace)):
                makers.append(maker)
        elif isinstance(expression, ast.Subscript):
            keys |= _strings(_resolve(expression.value, namespace))
        elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            items = _loop_items(call, expression.value.id, parents, namespace)
            if isinstance(items, Iterable) and not isinstance(items, str):
                keys |= {
                    one
                    for item in items
                    if isinstance(one := getattr(item, expression.attr, None), str)
                }
            elif isinstance(value := _resolve(expression, namespace), str):
                keys.add(value)
        elif isinstance(value := _resolve(expression, namespace), str):
            keys.add(value)
    return keys, makers


def _server_modules() -> list[tuple[str, Namespace]]:
    """Every server module's source beside the namespace it runs with.

    A module the app never imports gets an empty namespace, so only its literal strings can count:
    whatever it reads through a constant, it reads in code that does not run.
    """
    found: list[tuple[str, Namespace]] = []
    for path in sorted(SERVER.rglob("*.py")):
        if "tests" in path.parts:
            continue
        parts = path.relative_to(SERVER.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        module = sys.modules.get(name)
        found.append((path.read_text(encoding="utf-8"), vars(module) if module else {}))
    return found


def _client_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in client_source(CLIENT, ".ts", ".svelte")
    )


def unread_settings(
    registered: Mapping[str, ReadBy],
    modules: Iterable[tuple[str, Namespace]],
    client: str,
) -> tuple[list[str], list[str]]:
    """The settings nothing on their own side reads, and the settings this gate cannot find.

    Every registered setting is asked about by its KEY STRING, which is what the registry holds,
    not by a constant, which forty-four settings do not have. The second list is the guard on that
    promise: a registration written in a shape `located` does not know is named there rather than
    skipped, so a setting can no longer be invisible to this check without the check saying so.
    """
    every = list(modules)
    wanted = set(registered)
    keys: set[str] = set()
    makers: list[object] = []
    containers: dict[int, set[str]] = {}
    for source, namespace in every:
        found, built_by = located(source, namespace)
        keys |= found
        makers += built_by
        containers.update(declared_containers(ast.parse(source), namespace))

    patterns = [(maker, maker_pattern(maker)) for maker in makers]
    made_by: dict[str, int] = {}
    for key in wanted - keys:
        for maker, pattern in patterns:
            if pattern is not None and pattern.fullmatch(key):
                made_by[key] = id(maker)
    unfound = sorted(wanted - keys - set(made_by))

    read: set[str] = set()
    called: set[int] = set()
    # A read that only reports the value, by the fields it lands in. It counts when the client
    # reads one of them (see `landing`, and the planted offer below that no screen draws).
    reported: dict[str, set[str]] = {}
    for source, namespace in every:
        tree = ast.parse(source)
        parents = {
            id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node, keys, maker in _read_sites(tree, namespace, wanted, makers, containers):
            if maker is not None:
                called.add(maker)
                continue
            lands = landing(node, tree, namespace, parents)
            if lands is None:
                read |= keys
            else:
                for key in keys:
                    reported.setdefault(key, set()).update(lands)

    def is_read(key: str, side: ReadBy) -> bool:
        if side is not ReadBy.SERVER:
            return key in client
        if key in read or made_by.get(key) in called:
            return True
        return any(client_reads_field(field, client) for field in reported.get(key, ()))

    unread = sorted(
        key for key, side in registered.items() if key not in unfound and not is_read(key, side)
    )
    return unread, unfound


@pytest.mark.regression
def test_every_setting_is_read_by_something() -> None:
    """Importing the app first, so every feature has registered what it declares."""
    create_app()
    registered = {key: one.read_by for key, one in registered_settings().items()}

    # The side that says it acts on it, and no other. See the header: accepting either side would
    # make the client half unconditionally true, because a screen names every setting it draws.
    unread, unfound = unread_settings(registered, _server_modules(), _client_text())
    inert = [key for key in unread if key not in CHANGES_NOTHING_ON_PURPOSE]

    assert not unfound, (
        "\nThis gate cannot find how these settings are registered, so it cannot say whether\n"
        "anything reads them. Register the key through a constant, a literal, an entry of a\n"
        "declared map, a field of a declared list, or a function returning an f-string with a\n"
        "fixed head, or teach `located` the new shape.\n\n  " + "\n  ".join(unfound) + "\n"
    )
    assert not inert, (
        "\nThese settings are declared, drawn on a screen and read by nothing.\n\n"
        "Pressing one stores a value and changes no behaviour anywhere, which looks exactly\n"
        "like a working control, because the control IS working. Either read it where it is\n"
        "meant to take effect, mark it read_by=ReadBy.CLIENT if the browser is what acts on it,\n"
        "or add it to CHANGES_NOTHING_ON_PURPOSE with the reason.\n\n  " + "\n  ".join(inert) + "\n"
    )


def test_every_registered_setting_is_asked_about() -> None:
    """Every registered setting is asked about by its key, including those with no `*_KEY` constant
    or sharing a constant's name."""
    create_app()
    registered = {key: one.read_by for key, one in registered_settings().items()}
    _unread, unfound = unread_settings(registered, _server_modules(), _client_text())
    assert len(registered) >= 167, f"only {len(registered)} settings registered"
    assert unfound == []


def test_a_setting_the_browser_only_draws_is_not_counted_as_read() -> None:
    """A setting the browser only draws is not read, held as a test.

    Every screen names the settings it renders. So a check that accepted "the key appears in the
    client" would accept every setting there is, and the server half would never have to hold.
    """
    create_app()
    registered = registered_settings()
    server_side = [key for key, one in registered.items() if one.read_by is ReadBy.SERVER]

    assert server_side, "no setting says the server acts on it, so the server half checks nothing"
    # And the client half is checked against the client, rather than against whichever side passes.
    assert all(one.read_by in (ReadBy.SERVER, ReadBy.CLIENT) for one in registered.values())
    declared = 'LOUD_KEY = "playback.loud"\nregister_setting(key=LOUD_KEY)\n'
    unread, _ = unread_settings(
        {"playback.loud": ReadBy.SERVER},
        [(declared, {"LOUD_KEY": "playback.loud"})],
        client="const drawn = 'playback.loud';",
    )
    assert unread == ["playback.loud"]


def test_the_excuses_are_all_still_registered_settings() -> None:
    create_app()
    stale = sorted(set(CHANGES_NOTHING_ON_PURPOSE) - set(registered_settings()))
    assert not stale, f"these are not settings any more: {stale}"


def test_declaring_a_setting_is_not_counted_as_reading_it() -> None:
    """The one exclusion the whole gate rests on. Without it everything passes, forever."""
    only_declared = textwrap.dedent("""
        LOUD_KEY = "playback.loud"
        WATCHED = (LOUD_KEY,)
        class Keys:
            LOUD = "playback.loud"
        register_setting(key=LOUD_KEY, scope="user", default=False)
    """)
    namespace = {"LOUD_KEY": "playback.loud", "WATCHED": ("playback.loud",)}
    assert reads(only_declared, namespace, wanted={"playback.loud"}) == (set(), set())
    assert located(only_declared, namespace) == ({"playback.loud"}, [])


def test_the_ways_a_setting_is_actually_read_all_count() -> None:
    """Every shape in the tree: missing the map from a job type would report live settings dead."""
    namespace: dict[str, object] = {"LOUD_KEY": "playback.loud"}
    passed_to_a_reader = "async def f():\n    await settings.get_app(LOUD_KEY)"
    mapped_from_a_job_type = "def f():\n    keys = {media_jobs.THUMBNAIL: LOUD_KEY}"
    looked_up = "def f(values):\n    return values[LOUD_KEY]"
    the_literal = "async def f():\n    await settings.get_app('playback.loud')"
    through_a_module = "async def f():\n    await settings.get_app(player.LOUD_KEY)"
    player = ModuleType("player")
    player.LOUD_KEY = "playback.loud"  # type: ignore[attr-defined]

    for source in (passed_to_a_reader, mapped_from_a_job_type, looked_up, the_literal):
        assert reads(source, namespace, wanted={"playback.loud"})[0] == {"playback.loud"}, source
    found, _ = reads(through_a_module, {"player": player}, wanted={"playback.loud"})
    assert found == {"playback.loud"}


def test_a_declared_list_is_read_when_it_is_used() -> None:
    """Declaring `WATCHED` reads nothing; walking it later reads everything it was declared with."""
    declared = "WATCHED = (LOUD_KEY, QUIET_KEY)\n"
    used = declared + "def react(key):\n    return key in WATCHED\n"
    namespace = {
        "LOUD_KEY": "playback.loud",
        "QUIET_KEY": "playback.quiet",
        "WATCHED": ("playback.loud", "playback.quiet"),
    }
    wanted = {"playback.loud", "playback.quiet"}
    assert reads(used, namespace, wanted=wanted)[0] == wanted
    assert reads(declared, namespace, wanted=wanted)[0] == set()


def test_a_map_declared_in_one_module_is_read_in_another() -> None:
    """The four compression targets: declared as a map in `settings`, looked up in `service`."""
    targets = {"small": "compress.small"}
    declaring = 'KEY_FOR_PRESET = {"small": SMALL_KEY}\nregister_setting(key=SMALL_KEY)\n'
    looking_up = "async def f(preset):\n    await read(KEY_FOR_PRESET[preset])\n"
    unread, _ = unread_settings(
        {"compress.small": ReadBy.SERVER},
        [
            (declaring, {"SMALL_KEY": "compress.small", "KEY_FOR_PRESET": targets}),
            (looking_up, {"KEY_FOR_PRESET": targets}),
        ],
        client="",
    )
    assert unread == []


def test_a_registry_filled_at_run_time_does_not_vouch_for_every_setting() -> None:
    """What a container holds is read from its SOURCE, never from the live object.

    The registry is `_REGISTRY: dict[str, Setting] = {}` and every registration fills it, so a
    check that asked the live dict would count one `_REGISTRY[key]` as reading all of them.
    """
    source = "_REGISTRY = {}\ndef get(key):\n    return _REGISTRY[key]\n"
    namespace = {"_REGISTRY": {"playback.loud": object()}}
    assert reads(source, namespace, wanted={"playback.loud"})[0] == set()


def test_two_constants_with_one_name_are_two_settings() -> None:
    """Settings that share a constant's name are each read by their own key, not by a namesake."""
    reader = "async def f():\n    await settings.get_app(ENABLED_KEY)"
    declarer = 'ENABLED_KEY = "{}"\nregister_setting(key=ENABLED_KEY)\n'
    unread, unfound = unread_settings(
        {"semantic.enabled": ReadBy.SERVER, "watermarks.enabled": ReadBy.SERVER},
        [
            (reader, {"ENABLED_KEY": "semantic.enabled"}),
            (declarer.format("semantic.enabled"), {"ENABLED_KEY": "semantic.enabled"}),
            (declarer.format("watermarks.enabled"), {"ENABLED_KEY": "watermarks.enabled"}),
        ],
        client="",
    )
    assert (unread, unfound) == (["watermarks.enabled"], [])


def _strategy(subject: str, key: str) -> str:
    return f"enrich.{subject}.{key}"


def _invent(subject: str) -> str:
    return f"stash_boxes.invent.{subject}"


def test_a_setting_built_by_a_function_is_read_when_the_function_is_called() -> None:
    """Forty settings have no constant at all: they are made by `strategy_key` and read through it.

    Calling a maker vouches for its own settings and nobody else's (the pattern from its f-string
    says which are its), and calling it inside a registration vouches for nothing.
    """
    registering = textwrap.dedent("""
        for subject in SUBJECTS:
            register_setting(key=_strategy(subject, "birthdate"))
            register_setting(key=_invent(subject))
    """)
    reading = "async def f(subject):\n    await settings.get_app(_strategy(subject, 'birthdate'))"
    namespace = {"_strategy": _strategy, "_invent": _invent, "SUBJECTS": ("person",)}
    registered = {
        "enrich.person.birthdate": ReadBy.SERVER,
        "stash_boxes.invent.person": ReadBy.SERVER,
    }
    unread, unfound = unread_settings(
        registered, [(registering, namespace), (reading, namespace)], client=""
    )
    assert (unread, unfound) == (["stash_boxes.invent.person"], [])


def test_a_function_whose_key_starts_with_a_hole_is_not_a_pattern() -> None:
    """`f"{anything}"` would match every key there is, and vouch for all of them."""

    def anything(value: str) -> str:
        return f"{value}"

    assert maker_pattern(_strategy) is not None
    assert maker_pattern(anything) is None


def test_a_field_of_each_item_a_loop_walks_is_located() -> None:
    """The tile marks: seven settings registered in one loop, each through `_mark.key`."""
    source = "for _mark in MARKS:\n    register_setting(key=_mark.key)\n"
    marks = (SimpleNamespace(key="browse.one"), SimpleNamespace(key="browse.two"))
    namespace = {"MARKS": marks, "_mark": marks[-1]}
    assert located(source, namespace) == ({"browse.one", "browse.two"}, [])


def test_a_registration_in_a_shape_nobody_taught_it_is_named_not_skipped() -> None:
    """The guard on the promise that every setting is asked about."""
    source = "register_setting(key=compute()[0])\n"
    unread, unfound = unread_settings(
        {"mystery.key": ReadBy.SERVER}, [(source, {"compute": lambda: "mystery.key"})], client=""
    )
    assert unfound == ["mystery.key"]
    assert unread == []


class _Answer(Wire):
    """A reply with one field, for the planted cases below."""

    offer: bool = False


#: A setting read only to be reported: a private helper reads the key, its answer is
#: handed to a constructor by keyword, and the constructor puts it in a field of the reply.
_REPORTED_ONLY = textwrap.dedent("""
    register_setting(key=OFFER_KEY)

    async def _should_offer(hub):
        answered = str(await hub.get_app(OFFER_KEY))
        return answered != "done"

    def _view(*, offer):
        return _Answer(offer=offer)

    async def result(hub):
        return _view(offer=await _should_offer(hub))
""")


def test_a_setting_only_reported_to_a_screen_that_never_reads_it_is_not_read() -> None:
    """An offer nobody draws, planted. A route reads the key, so a looser gate would pass it; the
    only thing the value decides is a field no screen reads, so the switch changes nothing."""
    namespace = {"OFFER_KEY": "performance.offer", "_Answer": _Answer}
    registered = {"performance.offer": ReadBy.SERVER}

    unread, _ = unread_settings(registered, [(_REPORTED_ONLY, namespace)], client="const a = 1;")
    assert unread == ["performance.offer"]

    # A CSS class of the same name is not a screen reading the field.
    styled, _ = unread_settings(registered, [(_REPORTED_ONLY, namespace)], client=".offer { }")
    assert styled == ["performance.offer"]


def test_the_same_setting_counts_once_a_screen_reads_the_field() -> None:
    """The known positive beside the one above: followed to a screen that draws it, it is read."""
    namespace = {"OFFER_KEY": "performance.offer", "_Answer": _Answer}
    unread, unfound = unread_settings(
        {"performance.offer": ReadBy.SERVER},
        [(_REPORTED_ONLY, namespace)],
        client="{#if selfTest.offer}<Offer />{/if}",
    )
    assert (unread, unfound) == ([], [])


def test_a_read_that_decides_what_the_server_does_is_still_a_read() -> None:
    """Only a value that goes NOWHERE but into an answer is followed. One that decides a branch is
    the server acting on it, whatever else happens to it."""
    source = textwrap.dedent("""
        register_setting(key=OFFER_KEY)

        async def _limit(hub):
            return await hub.get_app(OFFER_KEY)

        async def run(hub):
            if await _limit(hub):
                start_the_work()
    """)
    unread, unfound = unread_settings(
        {"performance.offer": ReadBy.SERVER},
        [(source, {"OFFER_KEY": "performance.offer"})],
        client="",
    )
    assert (unread, unfound) == ([], [])


# --- the mirror question: can a PERSON change it
#
# A setting read by the server and on no screen can only be changed by editing the database: a
# decision made once by whoever wrote the default.

#: Settings deliberately not on any screen, and why.
#:
#: The reason is the point. A setting kept off a screen because it is dangerous is a decision worth
#: writing down; one kept off because somebody forgot is a bug wearing its costume.
NOT_ON_A_SCREEN_ON_PURPOSE: dict[str, str] = {
    # The Backup pane draws both, with its own controls and its own endpoint rather than the
    # settings one: a folder box and a how-many spinner. They are reachable; what is absent is the
    # literal key string, which is the only thing a checker can see.
    "backup.keep": "drawn by the Backup pane through its own endpoint",
    "backup.folder": "drawn by the Backup pane through its own endpoint",
    # Not a control at all. It records which version somebody dismissed the banner for, and the
    # dismissing IS the screen: there is nothing to put on a settings pane.
    "updates.dismissed_version": "state written by dismissing the banner, not a control",
    # Kept off the Identify pane, still read by the server: the model pair is a licensing choice
    # (`accurate` for non-commercial research, `permissive` for anything), and switching it
    # invalidates every stored embedding.
    "faces.model": "kept off the pane on purpose: a licensing choice, not a preference",
}


def _generic_sections() -> set[str]:
    """The section names rendered by a pane that draws whatever the server declares.

    A pane like Performance names its section once and renders every entry the server returns, so
    every setting in that section is reachable the moment it is declared. A pane that instead picks
    one key out of the list by name reaches only that key, and a setting declared into that
    section is drawn nowhere.

    Recognized by the pair: the section name it declares, and a read of the section's `settings`
    list. Either alone is not enough: a pane can name its keys and read the list too, but read it
    to `find` one entry rather than to render them all.
    """
    found: set[str] = set()
    for path in client_source(CLIENT, ".svelte", ".ts"):
        text = path.read_text(encoding="utf-8")
        if "section?.settings" not in text and "section.settings" not in text:
            continue
        for match in re.finditer(r"""SECTION\s*=\s*['"]([^'"]+)['"]""", text):
            found.add(match.group(1))
    return found


@pytest.mark.regression
def test_every_setting_is_on_a_screen_somebody_can_reach() -> None:
    create_app()
    registered = registered_settings()
    client = _client_text()
    generic = _generic_sections()

    unreachable: list[str] = []
    for key, setting in sorted(registered.items()):
        if key in NOT_ON_A_SCREEN_ON_PURPOSE:
            continue
        section = getattr(setting, "section", None)
        if section in generic:
            continue
        if key in client:
            continue
        unreachable.append(f"{key} (declared into section {section!r}, which nothing renders)")

    assert not unreachable, (
        "\nThese settings are declared and read, and appear on no screen.\n\n"
        "The server acts on the value, so the gate above is satisfied and nothing complains,\n"
        "but the only way to change one is to edit the database by hand, which means the default\n"
        "is the whole of the decision. Either render the section generically (see Performance),\n"
        "name the key in the pane that should show it, or say why in NOT_ON_A_SCREEN_ON_PURPOSE.\n\n  "
        + "\n  ".join(unreachable)
        + "\n"
    )


def test_the_screen_excuses_are_all_still_registered_settings() -> None:
    create_app()
    stale = sorted(set(NOT_ON_A_SCREEN_ON_PURPOSE) - set(registered_settings()))
    assert not stale, f"these are not settings any more: {stale}"


def test_a_pane_that_picks_one_key_is_not_counted_as_rendering_its_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinction the check rests on, planted rather than argued.

    Both files name a section and both read the settings list. Only one of them draws what it
    finds; the other looks one entry up by name and ignores the rest, which is exactly the shape
    that lets a setting be declared into a section nothing renders.
    """
    (tmp_path / "Renders.svelte").write_text(
        "const SECTION = 'Everything';\nentries = section?.settings ?? [];\n"
    )
    (tmp_path / "PicksOne.svelte").write_text(
        "const SECTION = 'JustTheOne';\n"
        "const entry = sections.flatMap((s) => s.settings ?? []).find((o) => o.key === SOME_KEY);\n"
    )
    monkeypatch.setattr(
        "tests.gates.test_no_setting_that_does_nothing.CLIENT", tmp_path, raising=False
    )
    import tests.gates.test_no_setting_that_does_nothing as module

    monkeypatch.setattr(module, "CLIENT", tmp_path)
    assert module._generic_sections() == {"Everything"}
