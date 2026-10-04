# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a swap never writes to a log, and the one call its lock never makes.

## No address, no port, no token, no secret, no code

The first requirement of a swap is that neither person learns the other's home address, ever, and
a log is the one place an address could be written by a line nobody reads twice: a warning with the
peer's address "for diagnosis", a hosting line with the public port, a refusal carrying the token it
refused. A log leaves the device in a diagnostics export, a pasted bug report, a backup. So every
log call in the swap slice and in the tunnel manager under it is read here, and each one may carry
only its event's name and fields that say what happened, never where or with what key:

- the first argument is a plain string, and there is no other positional argument (an f-string
  event or a `%s` argument could carry anything at all);
- no field is NAMED for an address, a port, a token, a secret, a key or the code;
- no field's VALUE reads a name like that either (`tunnel=hosting.public_ipv4` has an innocent key);
- no `**` spread, except the one helper named below with what it carries;
- and every field survives the kernel's redaction. A field the filter blanks by its name (anything
  named `session...`, for one, which it treats as a login) prints `[redacted]`, which is a line
  that says nothing while looking like it says something.

The scan is of the source, not of a run, so it covers the lines no test reaches: the refusals and
the failures, which are exactly the lines written in a hurry.

## The lock never loads a certificate

A TLS server context that loads a certificate as well as the key lets a client with NO key in: the
handshake simply takes the certificate path. So nothing in the slice may name the calls that load
one, spelled any way Python can spell a call: an attribute, an imported name, or a string handed to
`getattr`. This lives here and not beside the lock's own tests because those skip on an interpreter
without TLS keyed by a pre-shared key, and a rule about what the source SAYS must not depend on what
the interpreter can run.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from sift.kernel.log import REDACTED, redact

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[3]
SWAP = SOURCE / "slices" / "swap"
TUNNELS = SOURCE / "kernel" / "tunnels"

#: The logger methods. `bind` is here because a bound logger carries its fields into every line.
_LEVELS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "msg", "bind"}
)

#: The words a field's name, or a name its value reads, may not contain. Matched as whole words of
#: a snake_case identifier, so `skip` is not `ip` and `returncode` is not `code`.
FORBIDDEN_WORDS = frozenset(
    {
        # Where: the address, and every name the swap's code gives one.
        "address",
        "addr",
        "ip",
        "ipv4",
        "ipv6",
        "public",
        "external",
        "host",
        "endpoint",
        "gateway",
        "peername",
        "sockname",
        "proxy",
        "url",
        "uri",
        # The port the token carries, and the ports beside it.
        "port",
        "ports",
        "mapped",
        "mapping",
        "hosting",
        "listener",
        # With what: the token, its secret, the keys, the hello's nonce and signature, the code.
        "token",
        "secret",
        "psk",
        "key",
        "nonce",
        "sig",
        "signature",
        "code",
    }
)

#: The `**` spreads a log call may use, and what each carries.
SPREADS: dict[str, str] = {
    "_shape_of": (
        "a pasted tunnel configuration's section NAMES, field NAMES and line and byte counts,"
        " never a value; the tunnel's refusals log it so a refusal can be diagnosed"
    ),
}


@dataclass(frozen=True, slots=True)
class Said:
    """One problem with one log call."""

    where: str
    what: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _words(identifier: str) -> set[str]:
    return {part for part in identifier.lower().split("_") if part}


def _names_read(node: ast.AST) -> set[str]:
    """Every identifier an expression reads: its names, its attributes, and any string in it
    shaped like one (`get_extra_info("peername")` asks for an address by a string)."""
    found: set[str] = set()
    for one in ast.walk(node):
        if isinstance(one, ast.Name):
            found.add(one.id)
        elif isinstance(one, ast.Attribute):
            found.add(one.attr)
        elif (
            isinstance(one, ast.Constant)
            and isinstance(one.value, str)
            and _IDENTIFIER.fullmatch(one.value)
        ):
            found.add(one.value)
    return found


def _loggers(tree: ast.Module) -> set[str]:
    """The names a module binds a logger to (`log = get_logger(__name__)`)."""
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "get_logger"
        ):
            names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
    return names


def problems_in(source: str, where: str) -> tuple[int, list[Said]]:
    """How many log calls a module makes, and what is wrong with each one that is wrong."""
    tree = ast.parse(source)
    loggers = _loggers(tree) | {"log"}
    calls = 0
    said: list[Said] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in loggers
        ):
            continue
        calls += 1
        at = f"{where}:{node.lineno}"
        event = node.args[0] if node.args else None
        if node.func.attr != "bind" and not (
            isinstance(event, ast.Constant) and isinstance(event.value, str)
        ):
            said.append(Said(at, "the event is not a plain string"))
        if len(node.args) > (0 if node.func.attr == "bind" else 1):
            said.append(Said(at, "a positional argument after the event"))
        for keyword in node.keywords:
            if keyword.arg is None:
                spread = keyword.value
                helper = (
                    spread.func.id
                    if isinstance(spread, ast.Call) and isinstance(spread.func, ast.Name)
                    else None
                )
                if helper not in SPREADS:
                    said.append(Said(at, "a ** spread nobody has said is safe"))
                continue
            named = _words(keyword.arg) & FORBIDDEN_WORDS
            if named:
                said.append(Said(at, f"the field {keyword.arg!r} is named for {sorted(named)}"))
            if redact("probe", keyword.arg) == REDACTED:
                said.append(Said(at, f"the field {keyword.arg!r} is blanked by the redaction"))
            read = {name for name in _names_read(keyword.value) if _words(name) & FORBIDDEN_WORDS}
            if read:
                said.append(Said(at, f"the field {keyword.arg!r} reads {sorted(read)}"))
    return calls, said


def _modules() -> list[Path]:
    return sorted([*SWAP.glob("*.py"), *TUNNELS.glob("*.py")])


def _walk() -> tuple[int, list[Said]]:
    total = 0
    said: list[Said] = []
    for path in _modules():
        calls, found = problems_in(
            path.read_text(encoding="utf-8"), path.relative_to(SOURCE).as_posix()
        )
        total += calls
        said += found
    return total, said


def test_no_swap_or_tunnel_log_line_carries_an_address_a_port_a_token_or_a_code() -> None:
    _, said = _walk()
    assert not said, (
        "\nThese log calls could write where a swap's other end is, or with what key it locked.\n"
        "Log the event and what happened (the swap's short id, a count, a reason), never the\n"
        "address, the port, the token, its secret or the code.\n\n  "
        + "\n  ".join(str(one) for one in said)
        + "\n"
    )


def test_every_module_logs_through_one_name() -> None:
    """A logger bound to another name is a set of calls the walk above never reads."""
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert _loggers(tree) <= {"log"}, f"{path.name} binds its logger to {_loggers(tree)}"


def test_the_walk_reads_the_calls_it_is_about() -> None:
    """A walk that found nothing would pass for ever."""
    total, _ = _walk()
    assert total >= 40, f"only {total} log calls found; the walk is broken"


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ('log.info("tunnel.hosting", port=hosting.external_port)', "named for"),
        ('log.info("tunnel.hosting", tunnel=hosting.public_ipv4)', "reads"),
        (
            'log.warning("swap.lie", swap=live.short_id, peer=writer.get_extra_info("peername"))',
            "reads",
        ),
        ('log.info(f"swap.joined {token.text}")', "not a plain string"),
        ('log.info("swap.joined", token)', "positional"),
        ('log.info("swap.joined", **vars(hosting))', "spread"),
        ('log.info("swap.joined", session=live.short_id)', "blanked"),
        ('log.info("swap.code", code=facts.code)', "named for"),
    ],
)
def test_the_walk_refuses_each_way_a_line_could_say_too_much(line: str, why: str) -> None:
    """The known positives: each shape the rule is about, planted, is found."""
    _, said = problems_in(f"log = get_logger(__name__)\n{line}\n", "planted.py")
    assert said, f"the walk passed {line}"
    assert all(why in str(one) for one in said[:1])


def test_the_walk_passes_the_shape_every_swap_line_has() -> None:
    line = 'log.info("swap.ended", swap=self.short_id, files=len(self.done_files), reason=reason)'
    assert problems_in(line, "planted.py") == (1, [])


#: Every spelling of loading a certificate or building a context that would.
CERTIFICATE_CALLS = frozenset(
    {"load_cert_chain", "load_default_certs", "create_default_context", "wrap_socket"}
)


def _certificate_calls(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in CERTIFICATE_CALLS:
            found.append(f"{node.lineno} .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in CERTIFICATE_CALLS:
            found.append(f"{node.lineno} {node.id}")
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in CERTIFICATE_CALLS:
            found.append(f"{node.lineno} import {node.name}")
        elif isinstance(node, ast.Constant) and node.value in CERTIFICATE_CALLS:
            found.append(f"{node.lineno} {node.value!r}")
    return found


def test_the_lock_never_loads_a_certificate() -> None:
    """The server context is the key and nothing else. The lock's own file first, as text."""
    lock = (SWAP / "lock.py").read_text(encoding="utf-8")
    assert "load_cert_chain" not in lock
    assert re.search(r"SSLContext\(ssl\.PROTOCOL_TLS_SERVER\)", lock), "the server context moved"


def test_nothing_in_the_swap_loads_a_certificate_however_it_is_spelled() -> None:
    found = {
        path.name: calls
        for path in sorted(SWAP.glob("*.py"))
        if (calls := _certificate_calls(path.read_text(encoding="utf-8")))
    }
    assert not found, found


@pytest.mark.parametrize(
    "line",
    [
        "context.load_cert_chain(cert, key)",
        "from ssl import create_default_context",
        'getattr(context, "load_default_certs")()',
        "context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)",
    ],
)
def test_the_certificate_scan_finds_each_spelling(line: str) -> None:
    assert _certificate_calls(line)
