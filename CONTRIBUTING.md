# Contributing to Sift

Thank you for looking. Sift has one maintainer, so this page is about what makes a change quick to
review: how to build Sift, the checks every change runs, and how to write the code, the copy and
the commit.

## Before you start

- **A bug**: open an issue with the steps that show it. A security problem goes through
  [SECURITY.md](SECURITY.md), never a public issue.
- **A feature, or anything larger than a fix**: open an issue first and say what you want to change
  and why. A pull request that arrives with no discussion may not fit where Sift is going.
- **The code**: [ARCHITECTURE.md](ARCHITECTURE.md) sets out how it's put together and which check
  holds which rule.

## Build and run Sift

You need Python 3.13, [uv](https://docs.astral.sh/uv/) and Node.js 22 (the exact version is in
`.nvmrc`). Python 3.13 is the version the installer carries, and the first with the pre-shared-key
TLS a swap uses between two devices. From a clone of the repository:

```sh
uv sync --frozen --extra dev                 # the exact versions in uv.lock, with the checks
uv run pre-commit install --install-hooks    # the commit, commit-message and pre-push hooks
(cd frontend && npm ci && npm run build)     # the browser client, built into src/sift/web
uv run sift                                  # the server, on http://127.0.0.1:5171
```

On Windows, `uv run python scripts/fetch_vendor.py` downloads ffmpeg, the libwebp tools, yt-dlp,
gallery-dl and QuickJS, and checks each against a pinned SHA-256. It also checks the two things this
repository builds itself, the tunnel client and the HEIF reader's wheel, and `--build-missing` builds
one that isn't there. Anywhere but the computer that builds Sift's releases, add
`--wheel-by-contents` as well. Your compiler's wheel won't match the release's digest, so the script
checks the libraries inside it instead. On other operating systems, Sift uses the copies on your
`PATH`. The desktop app is in `desktop/`, with its own README, and `scripts/release.py` builds the
installer.

### SQLite

Sift uses the SQLite that comes with your Python, and checks at startup how it was compiled:

- **FTS5** is required. Every search runs against it, so Sift stops at startup without it and says
  why.
- **Extension loading** is optional. Sift starts without it, and the startup log says it's missing.

Both appear in the startup log with the version, and in `/health` when an admin asks for it. The
database runs in WAL mode, so prefer a SQLite with the fix for the WAL checkpoint race (3.51.3,
3.50.7 or 3.44.6) where your system offers one.

### Dependencies

Every Python dependency is pinned in `uv.lock`, and CI installs from it. Add a dependency to
`pyproject.toml`, then run `uv lock`.

```sh
uv run pip-audit            # known vulnerabilities in the locked versions
./scripts/sbom.sh           # writes sbom.cdx.json (CycloneDX)
```

The tools the installer carries aren't Python packages; [NOTICE](NOTICE) lists them with their
licenses.

### The documentation

[Sift's documentation](https://nuvibes.github.io/sift/) is built from `docs-site/` with Astro and
Starlight. `scripts/docs_generate.py` writes the pages that describe something declared elsewhere:
each Settings page from the settings Sift registers, around an opening written by hand in
`docs-site/openings/settings/`; What's new from `CHANGELOG.md`; and the API reference from
`frontend/openapi.json`. Every other page is written by hand in `docs-site/src/content/docs/`.

```sh
(cd docs-site && npm ci && npm run build && npm run preview)   # the site, on http://localhost:4321/sift/
uv run python scripts/docs_generate.py                         # write the generated pages again
uv run python scripts/check_docs.py                            # the docs checks
```

The docs checks need [Vale](https://vale.sh/) 3.24.0, on your `PATH` or named in `DOCS_VALE`. The
commit hook runs them whenever a change touches the docs, the server or the client's settings.

## The checks

The hooks run at every commit and again before every push. `uv run pre-commit run --all-files`
runs them by hand.

- **At every commit and push**: `gitleaks` on the changed files, `shellcheck`, the repository
  hygiene rules, the dash rule, the code-shape ratchet, `semgrep` with Sift's own rules, `ruff`
  (lint and format), `prettier` on the browser client, and the client's structural gates; at the
  commit, the entries the repository root may hold.
- **At the commit message**: the subject's shape and the narration list (see below).
- **Before every push**: `gitleaks` over the whole history.
- **In CI on every push**: all of the above, `mypy --strict` as Windows reads the code, the client's
  types and unit tests, the client's types against the server's description of its routes, the
  dependency audits, and the repository's gates in `tests/gates/`.

`./scripts/ci-local.sh` runs the quick checks the way CI does, and `./scripts/ci-local.sh --all`
runs everything, coverage included.

Every rule in `tests/gates/` and `semgrep/` is proved able to fail: each has a test that plants the
mistake and expects the rule to catch it. If you add a rule, add that test with it. A check that
can't fail is worse than none, because its green result is what stops anyone looking.

### The tests

The server's whole suite takes more than an hour, so while you work, run the tests beside what you
changed:

```sh
uv run pytest -p no:cacheprovider src/sift/slices/tags_ratings/tests   # one part of the server
cd frontend && npm run test:unit                                       # the browser client
cd frontend && npm run test:e2e                                        # the browser suite (slowest)
```

## Writing the code

- **A comment says why the code is as it is**, in a short paragraph at most. What the code does is
  the code's job. Measurements, the story of a change and the alternatives turned down go in the
  commit message and the pull request, never in the tree. No dates, and no references to anything
  outside this repository. `tests/gates/data/narration.json` lists the words the checks refuse.
- **A docstring states what a thing is for**, and the reason behind it when that isn't obvious.
- **A ratchet holds both.** `scripts/check_code_shape.py` (the server) and
  `frontend/scripts/check_code_shape.js` (the browser client and the desktop app) record every file
  whose comments are more than a quarter of its lines, every module over 1,000 lines, every Python
  function over 80 and every test file over 2,000. They also record every function whose
  cyclomatic complexity is over 10 in Python (counted as ruff's C901 counts it) or over 20 in
  TypeScript (branches, loops, catches, cases and short-circuits, each function by itself). A
  recorded number may only fall, and the hooks record a fall for you.
- **The source is ASCII,** and two hyphens are never a dash, in code, comments or copy. Text on
  screen writes a real em dash as `\u2014`.
- **Every Python file starts with** `# SPDX-License-Identifier: AGPL-3.0-or-later`.

## Writing the words on screen

- **Use Sift's words.** `tests/gates/data/vocabulary.json` lists them: the name of each thing (Site,
  Username, People, User, Cookies, Loop, stash-box, Hidden), the words retired in their favor, and
  the verbs a press may open with. The vocabulary gate reads every string a person sees. Search the
  client for a word before you name a thing.
- **Sentence case, American spelling and contractions**, at most 25 words a sentence. A name keeps
  its capital wherever it stands (Site, Photo Set, Tasks and Activity).
- **A place in Sift is named by its breadcrumb**, the path the settings search takes pasted. On
  screen a setting is a `SettingLink`, which opens Settings at that row. In the documentation site
  the path links to the row on its page:
  `[Settings > Tasks and Activity > Logs](/settings/tasks#activity.log)`. In this repository's own
  documents it stands in code ticks: `Settings > Tasks and Activity > Logs`.
- **Names in tests and examples are invented.** Use the people, Sites and usernames in
  `tests/gates/data/names_cast.txt`, or add a new invented one there. Never a real person, username,
  media id, address or path from your own computer.
- **A change to a screen, a setting or a menu comes with its docs change**, on the page of Sift's
  documentation that describes it.

## Commits and pull requests

- **The subject is `type(area): the change`**, at most 72 characters, such as
  `fix(theater): shuffle keeps the current clip`. It uses a fixed list of types: `feat`, `fix`,
  `chore`, `docs`, `test`, `refactor`, `perf`, `build` and `ci`. The area is one lower-case word,
  hyphenated when it needs more, and left out when a change has none: `build: Sift 0.2.1`. A
  body, where one is needed, is plain lines of fact. The commit-message hook checks all of it.

- **One change a pull request, with its tests.** If it changes what's on screen, say what to click
  to see it.

## Contributions written with a language model

A change written with a language model is welcome after a person has reviewed it. The person who
opens the pull request has read every line, understands it, ran the checks, and can answer
questions about it. The same goes for an issue or a review: check what it says before you post it.

## The HTTP API is internal

The routes under `/api` serve Sift's own browser client and desktop app. They aren't a public API:
they change between releases without notice, and `frontend/openapi.json` describes them for the
client's own types.

## License

Sift is licensed under AGPL-3.0-or-later, and a contribution is made under the same license.
