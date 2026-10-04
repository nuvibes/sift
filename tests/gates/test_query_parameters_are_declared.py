# SPDX-License-Identifier: AGPL-3.0-or-later
"""A query parameter no route declares is discarded in silence, and takes its test with it.

FastAPI throws away parameters a handler does not name, so a test asking for a narrowed page gets
the whole population and can still pass. The names are read from the schema the client is generated
from. The media grid's routes read their raw query as the search LANGUAGE, so there a name is
checked against what the parser recognises.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from sift.slices.search.filters import ALIASES, Field

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "sift"
SCHEMA = ROOT / "frontend" / "openapi.json"

#: The methods a test calls a route with, plus `request`, which takes the verb as its first word.
_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}

#: Routes that read the raw query string as a search query: `/api/loops` takes the bar's filters
#: on its files' facets.
_QUERY_LANGUAGE = {"/api/assets", "/api/assets/random", "/api/loops"}


def _recognised_filters() -> set[str]:
    """Every name the query language answers to, read from the parser's enum."""
    return {"q"} | {field.value for field in Field} | set(ALIASES)


def declared_parameters(schema: dict[str, object]) -> dict[tuple[str, str], set[str]]:
    """Every query parameter each route declares, keyed by verb and path template."""
    found: dict[tuple[str, str], set[str]] = {}
    paths = schema["paths"]
    assert isinstance(paths, dict)
    for template, operations in paths.items():
        for method, operation in operations.items():
            if method.lower() not in _VERBS:
                continue
            parameters = operation.get("parameters", []) if isinstance(operation, dict) else []
            found[(method.upper(), template)] = {
                one["name"] for one in parameters if one.get("in") == "query"
            }
    return found


def template_of(literal: str, templates: list[str]) -> str | None:
    """The route template a literal path belongs to: a literal segment beats a placeholder, as in
    the router (`/api/assets/random` is its own route), not ordering by length."""
    literal = literal.split("?", 1)[0].rstrip("/") or "/"
    for template in sorted(templates, key=lambda one: (one.count("{"), -len(one))):
        pattern = (
            "^"
            + re.sub(
                r"\{[^}]+\}", "[^/]+", re.escape(template).replace("\\{", "{").replace("\\}", "}")
            )
            + "$"
        )
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", pattern)
        if re.match(pattern, literal):
            return template
    return None


def _keys_sent(node: ast.Call, literal: str) -> set[str]:
    """The query keys one request call sends, from `params={...}` and from the address itself."""
    keys: set[str] = set()
    for keyword in node.keywords:
        if keyword.arg == "params" and isinstance(keyword.value, ast.Dict):
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    if "?" in literal:
        for pair in literal.split("?", 1)[1].split("&"):
            if "=" in pair:
                keys.add(pair.split("=", 1)[0])
    return keys


def requests_in(text: str) -> list[tuple[int, str, str, set[str]]]:
    """Every request in one file whose verb, address and query keys are all literal."""
    found: list[tuple[int, str, str, set[str]]] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attribute = node.func.attr
        if attribute == "request":
            if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
                continue
            verb, address = str(node.args[0].value).upper(), node.args[1]
        elif attribute in _VERBS:
            verb, address = attribute.upper(), node.args[0] if node.args else None
        else:
            continue
        if not isinstance(address, ast.Constant) or not isinstance(address.value, str):
            continue
        if not address.value.startswith("/"):
            continue
        keys = _keys_sent(node, address.value)
        if keys:
            found.append((node.lineno, verb, address.value, keys))
    return found


def test_every_query_parameter_a_test_sends_is_one_its_route_reads() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    declared = declared_parameters(schema)
    templates = list(schema["paths"])
    recognised = _recognised_filters()

    discarded: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" not in path.parts:
            continue
        for line, verb, literal, keys in requests_in(path.read_text(encoding="utf-8")):
            template = template_of(literal, templates)
            if template is None:
                continue
            allowed = declared.get((verb, template))
            if allowed is None:
                continue
            if template in _QUERY_LANGUAGE:
                allowed = allowed | recognised
            unknown = sorted(keys - allowed)
            if unknown:
                where = path.relative_to(ROOT)
                discarded.append(f"{where}:{line} sends {unknown} to {verb} {template}")

    assert not discarded, (
        "\nThese send a query parameter the route does not declare. FastAPI discards it in\n"
        "silence, so the request is answered over the WHOLE population and the test passes\n"
        "having proved nothing about the filtering it names.\n\n  " + "\n  ".join(discarded) + "\n"
    )


# --- the check can fail ------------------------------------------------------------------------


def test_a_parameter_no_route_declares_is_found() -> None:
    """A check that has never failed is indistinguishable from one that cannot."""
    planted = 'client.get("/api/jobs", params={"job_type": "probe"})\n'

    (found,) = requests_in(planted)

    assert found[1:] == ("GET", "/api/jobs", {"job_type"})


def test_a_parameter_written_into_the_address_is_read_too() -> None:
    """Half the suite writes the query into the address rather than passing a dictionary, and a
    check that only read one of the two spellings would be blind to the other."""
    planted = 'client.get("/api/jobs?job_type=probe&limit=2")\n'

    (found,) = requests_in(planted)

    assert found[3] == {"job_type", "limit"}


def test_a_request_made_through_request_is_read() -> None:
    """The authorization sweep calls every route this way, and it is the one place in the suite
    where the verb is a value rather than the name of the method."""
    planted = 'client.request("DELETE", "/api/jobs", params={"type": "probe"})\n'

    (found,) = requests_in(planted)

    assert found[1:] == ("DELETE", "/api/jobs", {"type"})


def test_a_request_with_no_query_at_all_is_not_reported() -> None:
    assert requests_in('client.get("/api/jobs")\n') == []


def test_an_address_built_from_a_variable_is_left_alone() -> None:
    """It cannot be resolved to a route, and guessing would report working tests as broken."""
    assert requests_in('client.get(f"/api/jobs/{job_id}", params={"type": "probe"})\n') == []


def test_a_literal_path_is_matched_to_the_route_it_belongs_to() -> None:
    templates = ["/api/assets", "/api/assets/random", "/api/assets/{asset_id}"]

    assert template_of("/api/assets", templates) == "/api/assets"
    # The one that matters: the placeholder spelling is the LONGER string, so a rule that took the
    # longest match would read a route of its own as somebody else's id.
    assert template_of("/api/assets/random", templates) == "/api/assets/random"
    assert template_of("/api/assets/01HX", templates) == "/api/assets/{asset_id}"
    assert template_of("/api/nothing", templates) is None


def test_the_query_language_names_are_read_from_the_parser() -> None:
    """Written out by hand they would go stale in the direction of calling a working filter a
    typo, which is the failure a list beside the thing it describes always has."""
    recognised = _recognised_filters()

    assert "q" in recognised
    assert {field.value for field in Field} <= recognised
    assert set(ALIASES) <= recognised
