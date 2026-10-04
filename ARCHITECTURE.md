# Architecture

How Sift is put together, and the checks that keep it that way. Read it before you add a feature:
most of it is one idea applied everywhere, and the parts that look like restrictions are what keep
the code navigable.

## The shape

```
src/sift/
  main.py          the HTTP application and the process entry point
  wiring/          the composition root: one module per thing it wires
  composition.py   the few answers that need two features together
  kernel/          the foundation: everything a feature may depend on
  slices/          the features, one folder each
  client.py        serves the built browser client
  web/             the built browser client (generated)
  testing/         helpers the tests use to build a working library
frontend/          the browser client: SvelteKit, built to static files
desktop/           the desktop app: an Electron window, the installer and updates
shared/            the types the desktop app and the browser client both read
docs-site/         the documentation site: Astro and Starlight
docs/              the model licences
tests/             the repository's gates and the cross-feature tests
semgrep/           the semgrep rules for Sift's own invariants, with their tests
scripts/           the hooks, the release, the docs generator and the vendored tools' manifest
```

A running Sift is one Python process. It answers `/api` itself and hands out the built browser
client for everything else, on one port and one origin. The desktop app starts that process and
opens a window onto it; another device opens the same address in a browser.

## Two layers, and the one rule between them

**The kernel is the foundation.** The database, the job queue, the permission model, media
handling, hashing, paths, the settings registry, the ledger, logging. It knows nothing about any
feature.

**A slice is a feature.** Downloads, faces, sharing, search, Hidden, the Theater wall. Each is a
folder under `slices/` with the same parts: `router.py` for its routes, `service.py` for what it
does, `models.py` for what it sends and receives, and its own `tests/`. A slice that only reads
leaves out `service.py`; a slice with nothing to send leaves out `models.py`.

**The rule: a slice imports the kernel and nothing else built on it.** A feature that reaches into
another feature's internals couples the two, and they can no longer be built, tested or removed
apart.

Signing in is the one exception, because every scoped route needs to know who is asking. The
`auth` slice publishes what the others need from its package's front door, and nothing behind it.
`tests/gates/test_slice_isolation.py` reads every import in the tree and fails on any other.

```
   slices/faces  --+
   slices/search --+--> kernel --> the database, the filesystem, the tools
   slices/vault  --+
        |
        +-- never to each other
```

The kernel never imports a slice. If the foundation needs something only a feature knows, either
the thing belongs in the kernel or the feature hands it in.

## How features meet: seams and the composition root

A feature often needs another's work: search asks Smart Search what some words mean, faces asks a
stash-box for a person's pictures, a download is filed into a folder. Neither may import the other.

`kernel/seams/` holds a typed interface (a `Protocol`) for each of those needs. The slice that does
the work implements it; the slice that needs it depends on the interface alone. The composition
root, `wiring/`, builds every part at startup and hands each one to whoever asked, and
`kernel/wiring.py` declares every part once with its type, so a renamed part is a type error rather
than a failure on screen. `composition.py` holds the few answers that need two
features' tables together, such as turning a name a stash-box returned into a Person or a Site.

`wiring/` is the only place that names more than one slice, and it's written out: every router,
handler and service on a line of its own, with no discovery step. Reading it is reading the whole
application. A seam has no placeholder behind it: an interface with no backend is a boundary named
before it's needed, and a placeholder would be a claim that something works.

## Reading: one door

Every read of a file or a folder goes through `kernel/access`, and every read takes a viewer.
Nothing else may write SQL against those tables; the semgrep rule `sift-no-asset-sql-outside-kernel`
holds it.

An unauthorized read is served when two paths come to disagree about who may see what. So the
grid, the single-file read and the permission check are the same statement with different
parameters. A guest sees nothing by default; a share opens something up; a restrict closes it,
and a share made elsewhere can't reopen it. Hidden conceals from every User, admins included, until
it's unlocked.

The permission rule is written once, as fixed SQL fragments that `kernel/sql_splice.py` splices
into each statement when the module loads. A statement is a module constant and every value is a
bound parameter: **query text is never built from anything a request carries**. The access tests
hold every statement to the same answers.

## Writing: one writer, one record, one announcement

- **One writer.** Every database write runs inside `Database.write()`, which lets one writer in at a
  time and commits or rolls back as a whole.
- **One record.** Everything that happens in the library is written through `kernel/ledger.py`: a
  closed list of verbs, a snapshot of the subject's name, and who did it. App History and each
  page's History read it.
- **One announcement.** A write a screen draws calls `announce()` inside its transaction, from
  `kernel/changes.py`. The message names what kind of thing changed, never its contents: the client
  asks the ordinary route again, through the permission layer, so a notification can never become
  a second answer to who may see what.

Two more doors stand between Sift and the world outside it:

- **Changing a file in a person's library** is decided in `kernel/library_write.py` and nowhere
  else. Sift runs as the person signed in to Windows, so this check is the only thing between a
  mistake and their files. `tests/gates/test_one_write_door.py` holds it.
- **Anything about the library leaving the computer** for a stash-box goes through one service,
  which refuses first for a file whose stash-box lookups are turned off.
  `tests/gates/test_one_outbound_door.py` holds it.

## Untrusted input has one entrance

Files arrive five ways: dropped in the browser, pasted, written by a downloader, found in a watched
folder, or uploaded. All of them go through `kernel/ingress.py` before anything hashes, indexes,
decodes or serves them. The file's own leading bytes decide what it is, never its name or the type
its sender claimed, and a second stage has ffprobe parse it before anything decodes it for real.
Nothing in Sift runs an ingested file.

## The database: one component per feature

Each feature declares its tables as a `SchemaComponent` in `kernel/db_schema.py`: a name, a
version, the steps that bring an older library forward, and the components it depends on. At
startup each component brings itself up to date in dependency order, and a library too old for this
build is refused before anything changes. `tests/gates/test_schema_shape_is_pinned.py` pins the
whole schema, so it can't change without a component's version moving.

## Background work: the job queue

Everything slow runs in `kernel/jobs/`. A job is a row in the database before it's anything else,
so a crash or a power cut loses at most the work in progress. A feature registers a handler with
its name at startup and enqueues by type; it never touches the `jobs` table. A payload carries ids,
never paths.

- **Families.** Each job type belongs to a family (Scan, Generate, Fingerprint, Identify, Smart
  Search, or Other), and Tasks and Activity draws one progress bar per family.
- **Schedules.** Work Sift does on a clock (a backup, a sweep, recognition held to its hours)
  declares itself in `kernel/jobs/schedules.py`, so one screen can show all of it.
- **Sharing the computer.** While a person uses the computer, background work steps back to a share
  of it (`kernel/budget.py`), and reads from one storage are capped so a network share isn't
  flooded (`kernel/lanes.py`).

Gates hold the queue to its screens: every job type has a name, every recurring job is declared,
and no job type is declared that nothing runs.

## Settings

A feature declares each setting with `register_setting` in `kernel/settings_registry.py`: its key,
default, label, help, and the section it belongs to. Settings is drawn from that registry, and
`tests/gates/test_every_setting_reaches_a_screen.py` fails on a setting no screen draws.

## The browser client

SvelteKit with Bits UI for the behavior that's hard to get right (focus traps, Escape, ARIA) and
plain scoped CSS for everything visible. There's no utility-class framework and no component
library's look; Sift's look is its own, built on tokens.

- **Color is layered.** Primitive values, then Sift's semantic names defined from them. A
  component reads a semantic name, never a raw color, and a gate refuses a color literal.
- **A thing that appears twice is a component.** A dialog, an error line, the actions on a file:
  written once in `frontend/src/lib/components/common/`, used everywhere.
- **The server's description of itself is the source of its shapes.** CI regenerates
  `frontend/openapi.json` from the running application and the client's types from it, and fails if
  either is out of date.

## Where the rules live

Each rule above is held by a check, and each check is proved by planting the mistake it exists to
catch.

- `tests/gates/`: the cross-cutting rules: the layering, the doors, every route reachable from a
  screen, every setting drawn, every job named, the schema pinned, the words on screen.
- `semgrep/`: pattern rules for Sift's own invariants, each with tests that it fires on a violation
  and stays quiet on the correct code.
- `frontend/scripts/gates.js`: the browser client's structural gates.
- `frontend/src/**/*.test.ts`: the client's unit tests, and the rules about components, color and
  the generated types.
- `frontend/e2e/`: the rules only a real browser can check.

## Adding a feature

1. If it belongs to an existing slice, add it there.
2. If it's new, make a folder under `slices/` with the same parts.
3. If it needs something the kernel doesn't have, add it to the kernel, or add a seam for the slice
   that does the work.
4. Declare its tables as a schema component, its settings with `register_setting` and its jobs with
   their names and families.
5. Register its router in `wiring/routes.py` and its parts in the `wiring/` module for its concern.
6. Add the tests beside it, and if the change brings a rule, the gate that holds it.

[CONTRIBUTING.md](CONTRIBUTING.md) has how to run the checks.
