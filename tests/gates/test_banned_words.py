# SPDX-License-Identifier: AGPL-3.0-or-later
"""Words this repository does not use anywhere, comments and docstrings included.

`test_one_word_per_thing.py` and the client's copy gate read only what a person can SEE, and exempt
comments, which define the terms. This is the smaller, harder list: words that have already become
a second name for something in the prose too, and read as ordinary English to whoever types them
next. One entry at a time, each with its reason:

- **catalogue**, for a stash-box: `stash-box` in prose, `stash_box` in an identifier. The American
  `catalog` is NOT banned: it means Sift's own catalog of media, consistently.
- **platform**, for a Site. Still right for the operating system (`sys.platform`,
  `process.platform`), and in a migration, which records what a database was.
- **The retired names of a username** (an "account" with a "handle") and **of the storage** in
  both senses (`accounts`, `handle`, `site_user_id`, the sign-in `account_id`, the old sign-in API).
  A statement that says one is history or will not run; the migrations are named in
  `EXEMPT_FILES`, and a retired trigger can only be dropped by its name.

The bare words "account" and "handle" are not banned: "handle" is a job handler, a file handle or
the verb nearly everywhere, and "account" still means an operating system's account, a login on
somebody else's website and the verb. Their site sense is held by this file's names and by the
copy gate.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.gates import vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]

#: Each entry is a pattern, what to write instead and, where it is not banned everywhere, the path
#: prefixes it applies under (`banned_everywhere` in `data/vocabulary.json`).
_ENTRIES = {entry["name"]: entry for entry in vocabulary.banned_everywhere()}

#: Identifiers, so `\b` either side: `AccountViewer` would not match.
RETIRED_USERNAME_NAMES: str = _ENTRIES["retired username names"]["pattern"]

#: CASE-SENSITIVE (`(?-i:`), because the scan is not and `_A_HANDLE` (a Windows handle in a watcher
#: test) is not `_a_handle`. A table name matches only after the SQL word that introduces one.
RETIRED_STORAGE_NAMES: str = _ENTRIES["retired storage names"]["pattern"]

BANNED: dict[str, str] = {entry["pattern"]: entry["instead"] for entry in _ENTRIES.values()}

#: Lines where a banned word is the RIGHT word, matched against the line itself. Per line, because
#: `catalog.py` says `platform` in eighty statements and `site` in four hundred sentences, and a
#: file-level exemption would stop reading the sentences.
EXEMPT: tuple[tuple[str, str], ...] = (
    # --- a gate that pins the old word's ABSENCE has to spell it
    (r"""assert '"platform"' not in""", "a gate reading that the retired word is gone"),
    # --- the stored query key of an older link, kept so the link still lands
    (r"platforms=", "the query language's older key, read for links that still carry it"),
    # --- the operating system, which is a different word for a different thing
    (
        r"sys\.platform|platform\.(system|machine|node|processor|architecture|release|uname|version"
        r"|python_\w+|freedesktop\w*)|platformdirs|process\.platform|navigator\.platform"
        r"|^\s*import platform\b|--python-platform|PLATFORM = \"x86_64"
        r"|setattr\(sys, \"platform\"",
        "the operating system",
    ),
    (
        r"\bplatform(s)?\b(?=[^\n]*\b(Windows|POSIX|win32|Linux|macOS|Electron|operating system"
        r"|ships on|junction|symlink|chmod|ffmpeg)\b)|(?<=\bthe )platform(?=[^\n]*\bSift ships\b)",
        "a sentence about the operating system",
    ),
    # --- the old word read once to be rewritten: a stored failure sentence, an old settle receipt
    (
        r"Platform Connection(?=[^\n]*\b(then try|cookies now)\b)|`platform` is read as `site`"
        r'|said == "platform"|"subject": "platform"',
        "the retired word, read once to be rewritten to Site",
    ),
    # --- a NAME written under the old word that something still has to find: a retired trigger, a
    # moved address, a key an older Sift wrote into somebody's browser. Literals only.
    (
        r"""\bvis_\w*(platforms?|accounts?)\w*\b|/platforms\b|['\"`]platforms['\"`]|\bplatforms:"""
        r"|\bmovedAddress\b",
        "a name something written under the old word still has to be found by",
    ),
    # --- a stored JSON PAYLOAD read back (a queued job, an undo receipt), which can say either
    # word. Only a comparison or a lookup, never a value being written.
    (
        # A test writing an OLD payload down to prove the reader still reads it.
        r"""==\s*["']platform["']|\.get\(\s*["']platform["']\s*\)|["']platform["']\s*:\s*["']""",
        "a stored payload read back, which can say either word",
    ),
    # --- a guard that refuses a container runtime has to name the socket and command it forbids.
    (
        r"""docker\.sock|/var/run/docker|DOCKER_HOST|["']docker["' ]|["']docker-compose["']""",
        "a guard that refuses a container runtime by name",
    ),
)

#: Where each banned word is banned; a word with no entry is banned everywhere. `catalogue` must
#: never get one. Outside its trees `platform` means the MACHINE: the CI workflows,
#: `pyproject.toml`, the shell scripts and the desktop's Electron main process.
SCOPED_TO: dict[str, tuple[str, ...]] = {
    entry["pattern"]: tuple(entry["scope"]) for entry in _ENTRIES.values() if entry["scope"]
}

#: Sentences that name the old word ON PURPOSE, by phrase as `ALLOWED` is in
#: `test_one_word_per_thing.py`: each explains that the word moved.
ALLOWED_PHRASES: tuple[str, ...] = (
    "Platforms became Sites",
    "was a Platform",
    "maps `platform` to Site",
    "`platforms` was that name",
    "rather than to `platforms`",
    "the old spelling",
    "/api/platformsomething",
    "/platformsish",
    "the old word",
    "until 0.1.153",
    "before 0.1.153",
    "says `platform`",
    "said `platform`",
    "spelled `platform`",
    "held `platform`",
    "holds `platform`",
)

#: Files whose whole subject is the MACHINE, Windows against POSIX, inside the scope above.
ABOUT_THE_MACHINE: dict[str, str] = {
    "src/sift/kernel/config.py": "where the settings file goes on each machine",
    "src/sift/kernel/content/mounts.py": "reading the machine's mount table",
    "src/sift/kernel/hardware.py": "what the processor and the memory call themselves",
    "src/sift/kernel/paths.py": "how each machine spells an absolute path",
    "src/sift/kernel/ports.py": "how each machine reports a port already in use",
    "src/sift/kernel/subprocess.py": "which priority tools the machine has",
    "src/sift/kernel/tests/test_config.py": "the same, tested on both",
    "src/sift/kernel/tests/test_ingress.py": "a FIFO, which one machine does not have",
    "src/sift/kernel/tests/test_library.py": "junctions and permission bits",
    "src/sift/kernel/tests/test_log.py": "how each machine spells a home directory",
    "src/sift/kernel/tests/test_paths.py": "the same rule proved on both",
    "src/sift/kernel/tests/test_ports.py": "the arm the shipping machine never runs",
    "src/sift/kernel/tests/test_content.py": "a skip that names the machine",
    "src/sift/kernel/tests/test_content_derivatives.py": "a skip that names the machine",
    "src/sift/slices/library_roots/browse.py": "drive letters",
    "src/sift/slices/library_roots/watcher.py": "whose change notifications can be used",
    "src/sift/slices/library_roots/tests/test_browse.py": "the same, with the machine faked",
    "src/sift/slices/library_roots/tests/test_jobs.py": "junctions and unreadable folders",
    "src/sift/slices/library_roots/tests/test_native_watch.py": "one machine's own watch",
    "src/sift/slices/library_roots/tests/test_quarantine.py": "permission bits",
    "src/sift/slices/library_roots/tests/test_watcher.py": "polling against native watching",
    "src/sift/slices/organize/tests/test_edges.py": "permission bits",
    "src/sift/slices/organize/tests/test_organize.py": "path separators",
    "src/sift/slices/player/tests/test_stream.py": "the process listing, which differs",
    "src/sift/slices/update_notify/service.py": "which kind of update this machine takes",
    "src/sift/slices/update_notify/tests/test_service.py": "the same",
    "src/sift/testing/tools.py": "planting a stand-in binary on either machine",
    "src/sift/slices/backup/tests/test_restore.py": "one guarantee proved on POSIX only",
    "tests/gates/__init__.py": "which gates a machine can run",
    "tests/gates/test_gates.py": "the same",
    "tests/gates/test_no_browser_stepper_arrows.py": "the number-box arrows the BROWSER draws",
}

#: Files whose whole subject is a MIGRATION, excused by NAME: a step records what a database WAS,
#: and a fresh install runs the version-1 CREATEs before every ALTER, so the old names must be
#: there. Only a `schema.py` and a migration TEST, which builds an old version's tables, qualify.
EXEMPT_FILES: tuple[str, ...] = (
    # --- the schemas
    "src/sift/kernel/access/schema.py",
    "src/sift/kernel/migrations.py",
    "src/sift/kernel/content/schema.py",
    "src/sift/slices/download/schema.py",
    "src/sift/slices/faces/schema.py",
    "src/sift/slices/search/schema.py",
    "src/sift/slices/stash_boxes/schema.py",
    "src/sift/slices/suggestions/schema.py",
    # --- the tests that build a table in the old words
    "src/sift/slices/download/tests/test_schema.py",
    "src/sift/slices/stash_boxes/tests/test_schema.py",
    # --- the username rename's steps and the tests that walk them
    "src/sift/slices/player/schema.py",
    "src/sift/slices/settings_hub/schema.py",
    "src/sift/slices/watermarks/schema.py",
    "src/sift/slices/workbench/schema.py",
    "src/sift/kernel/tests/test_content_stamps.py",
)

#: Where a banned word may survive: the gates that refuse it have to spell it. A build artifact is
#: not exempt: it carries the word only because its source did.
ALLOWED = (
    Path(__file__).name,
    "test_one_word_per_thing.py",
    "vocabulary.test.ts",
    "vocabulary.json",
    "ScreenWords.yml",
)

#: Only what is committed.
SUFFIXES = (".py", ".ts", ".svelte", ".css", ".md", ".sh", ".json", ".yml", ".yaml", ".toml")

#: Generated from the source: fixing the source fixes these on the next generation.
GENERATED = ("frontend/src/lib/api/schema.d.ts", "frontend/openapi.json", "src/sift/web/")


def _tracked() -> list[Path]:
    """Every file git is tracking."""
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [REPO / name for name in listed.stdout.split("\0") if name]


_EXEMPT = tuple((re.compile(pattern), reason) for pattern, reason in EXEMPT)


def deliberate(line: str) -> bool:
    """Whether this line is a sentence that names the old word on purpose."""
    return any(phrase in line for phrase in ALLOWED_PHRASES)


def excused(line: str) -> str | None:
    """Why this line may say a banned word, or None where it may not."""
    for pattern, reason in _EXEMPT:
        if pattern.search(line):
            return reason
    return None


@pytest.mark.parametrize("word", sorted(BANNED))
def test_a_banned_word_appears_nowhere(word: str) -> None:
    pattern = re.compile(word, re.IGNORECASE)
    offences: list[str] = []
    for path in _tracked():
        if path.suffix not in SUFFIXES or path.name in ALLOWED:
            continue
        where = path.relative_to(REPO).as_posix()
        if where.startswith(GENERATED) or where in EXEMPT_FILES:
            continue
        if where in ABOUT_THE_MACHINE:
            continue
        scope = SCOPED_TO.get(word)
        if scope is not None and not where.startswith(scope):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if pattern.search(line) and not excused(line) and not deliberate(line):
                offences.append(f"{where}:{number}  {line.strip()[:90]}")

    assert not offences, (
        f'"{word}" is not a word this repository uses. Write {BANNED[word]} instead.\n\n'
        + "\n".join(f"  {one}" for one in offences[:40])
        + (f"\n  ... and {len(offences) - 40} more" if len(offences) > 40 else "")
    )


def test_the_gate_can_actually_find_the_word() -> None:
    """The pattern finds the word: a search that matches nothing reads as one that found nothing."""
    pattern = re.compile("catalogue", re.IGNORECASE)
    assert pattern.search("asking the catalogues about it")
    assert pattern.search("One Catalogue's answer")
    assert not pattern.search("Sift's own catalog of media")


def test_the_old_word_for_a_site_is_caught_in_prose_and_in_a_name() -> None:
    """Both halves of the second entry, from sentences that were really in the tree."""
    pattern = re.compile("platform", re.IGNORECASE)
    for line in (
        "    #: The Platform a file came from, drawn as a chip.",
        "    async def list_platforms(self) -> list[Platform]:",
        "\t<h1>Platforms</h1>",
    ):
        assert pattern.search(line) and not excused(line), line


def test_the_retired_delivery_is_caught_and_its_guards_are_not() -> None:
    """The container image's old sentences are caught, and the guards that refuse a container
    runtime by name are excused."""
    pattern = re.compile(_ENTRIES["docker"]["pattern"], re.IGNORECASE)
    for line in (
        '"If you are running in Docker, check that this path is mounted and that the "',
        "'This is usually an older copy of Sift still running in Docker. Stop that, then ' +",
        "# Sift now ships two ways: a Docker image, where every path is a mount somebody chose,",
    ):
        assert pattern.search(line) and not excused(line), line
    for line in (
        '        assert "docker.sock" not in text, path',
        '    runtimes = ("docker ", "docker-compose", "podman", "nerdctl", "systemctl", "kubectl")',
    ):
        assert excused(line), line


def test_the_two_places_the_word_survives_are_each_proved() -> None:
    """The machine and a retired trigger name are excused; the storage's old names are not."""
    for line in (
        '    _WINDOWS = sys.platform == "win32"',
        "    if (process.platform === 'darwin') return;",
        '# `sys.platform == "win32"` and then calls the other platform\'s branch unreachable',
        '    "vis_acl_grants_insert_platform",',
        '    (API_PREFIX + "/platforms", API_PREFIX + "/sites"),',
        "    platforms: 'sites',",
        "    const now = movedAddress('platforms');",
    ):
        assert excused(line), line
    # The storage's own names are no longer excused outside a migration.
    for line in (
        '    "SELECT id FROM platforms WHERE name = ?"',
        "  LEFT JOIN platforms pl ON pl.id = ac.platform_id",
        '        _own_tags("platform_tags", "platform_id", "pl"),',
    ):
        assert not excused(line), line


def test_the_retired_username_names_are_caught_and_their_heirs_are_not() -> None:
    pattern = re.compile(RETIRED_USERNAME_NAMES, re.IGNORECASE)
    for line in (
        "from sift.slices.people.models import AccountWrite, PersonWrite",
        "    board.register(people.HandleQueue(part, access))",
        "\ttype Row = components['schemas']['AccountView'];",
        "    href=handle_opens(account.id, account.person_id)",
    ):
        assert pattern.search(line) and not excused(line), line
    for line in (
        "from sift.slices.people.models import UsernameWrite, PersonWrite",
        "    found: UsernameSuggestion | None",
        "\ttype Row = components['schemas']['UsernameView'];",
        "    href=username_opens(one.id, one.person_id)",
    ):
        assert not pattern.search(line), line


def test_bringing_files_in_is_importing_them() -> None:
    """Bringing files in is Importing: the four forms of the old phrase are caught, the words that
    replaced them and "into" are not."""
    pattern = re.compile(_ENTRIES["bring in"]["pattern"], re.IGNORECASE)
    for line in (
        'raise StashRefused("A Stash library is already being brought in.")',
        '    f"Brought in {tally.scenes_matched:,} of {tally.scenes:,} files and "',
        '"""What a Stash run could not bring in yet, kept until the file it belongs to arrives.',
        '// running scan does not read as "bringing in files".',
        "#: brings in thousands and only the surfaces that draw files care",
    ):
        assert pattern.search(line) and not excused(line), line
    for line in (
        'raise StashRefused("A Stash library is already being imported.")',
        "Imported 2 of 3 files.",
        "bringing into view the row that failed",
        "async function bringIn() {",
    ):
        assert not pattern.search(line), line


def test_the_retired_storage_names_are_caught_and_english_is_not() -> None:
    """The retired storage names are caught; the verb, the operating system and a Windows handle
    are not."""
    pattern = re.compile(RETIRED_STORAGE_NAMES, re.IGNORECASE)
    for line in (
        '    "SELECT id FROM accounts WHERE site_id = ? AND handle = ?"',
        '            "INSERT INTO asset_accounts (asset_id, account_id) VALUES (?, ?)",',
        "        seed_guest_account(db_path, A_GUEST_ACCOUNT)",
        "class ShareableAccountResponse(Wire):",
        '    ("GET", "/api/auth/users"): Case(Policy.ADMIN, response=AccountResponse),',
        "            site_user_id=where.number,",
        "        actor=Actor.user(one.id), made=Made(for_account=one.id)",
    ):
        assert pattern.search(line) and not excused(line), line
    for line in (
        "    # A face group somebody has already named accounts for this folder.",
        "    _A_HANDLE: Any = 1",
        "    emitter._whandle = _A_HANDLE",
        "    '\"The same escape by the redirection an ordinary Windows account can make.'",
        '    "SELECT id FROM usernames WHERE site_id = ? AND name = ?"',
        "class ShareableUserResponse(Wire):",
    ):
        assert not pattern.search(line), line
    assert excused('    "vis_asset_accounts_insert_before",')
