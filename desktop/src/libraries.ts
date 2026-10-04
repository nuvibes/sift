/* The libraries this copy has opened, and what has to be true before it opens another.
 *
 * ## Why the shell keeps this list rather than the application
 *
 * It is a list of other libraries. A list kept inside one of them could only be read while that one
 * was open, which is precisely the moment it is least useful, because switching away is the thing
 * it exists for. The shell is also the only thing that can act on it: starting a backend on a
 * different folder means stopping the one that is running, and only the process that started it can.
 *
 * ## Why a folder is asked about before anything is stopped
 *
 * Opening a Sift library MIGRATES it. The schema is brought up to whatever the running build
 * declares, in one direction, and an older Sift cannot read it afterwards. That is correct and it is
 * how every Sift start has always worked, but it must not happen to somebody who only meant to
 * look. So the target is read first, by a process that changes nothing, and an upgrade is offered
 * as a decision with a backup attached rather than performed as a side effect of a click.
 *
 * The reading is done by the BACKEND, not here, and that is not an arbitrary split. The answer is a
 * comparison against the schema registry, and the registry only exists once every slice has been
 * imported, which is a thing the Python side does and this one cannot. A second opinion written in
 * TypeScript would be a copy of the migration runner's own rule, kept in step by hand, and the day
 * it drifted it would send somebody into an upgrade the backend was about to refuse.
 */

import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { INTERPRETER_ARGS } from './backend';
import { log } from './log';
import { bundlePaths, type DataLocations } from './paths';
import type { KnownLibrary } from './settings';

/* The questions the backend answers without starting. Written out here and pinned by a test
 * against the Python that implements them (`libraries.test.ts`), for the same reason the preload's
 * channel names are: they are two copies of one string across a language boundary, and nothing
 * else would notice a change. See `INSPECT_LIBRARY` in sift/main.py.
 *
 * The first two take a library's data FOLDER or a database FILE; the third copies a database file
 * into a new library's data folder. */
export const INSPECT_FLAG = '--inspect-library';
export const BACK_UP_FLAG = '--back-up-library';
export const ADOPT_FLAG = '--adopt-library';

/** The name a library's own database has inside its data folder. `DATABASE_FILENAME` in db.py. */
export const DATABASE_FILENAME = 'sift.sqlite3';

/* The note a backend leaves in its own data folder, naming the library to start on, before it
 * stops asking to be started again. `HANDOFF_FILENAME` in sift/slices/backup/libraries.py, pinned
 * by a test in `libraries.test.ts` for the reason the flags above are. */
export const SWITCH_NOTE = 'library-switch.json';

/* The libraries folder's own mark, the folder's name beside a first library, and the key in the mark
 * naming the library chosen to open when Sift starts. `LIBRARIES_MARK` and `LIBRARIES_FOLDER` in
 * sift/kernel/config.py and `OPENS_AT_START` in sift/slices/backup/libraries.py, pinned by a test
 * in `libraries.test.ts` for the reason the flags above are. */
export const LIBRARIES_MARK = 'sift-libraries.json';
export const LIBRARIES_FOLDER = 'libraries';
export const OPENS_AT_START = 'opens_at_start';

/**
 * The folder libraries are made in, found from a library's data folder the way the backend finds it
 * (`libraries_folder` in sift/kernel/config.py): the folder two above a member's data folder when it
 * carries the mark, and `libraries` beside the data folder otherwise.
 */
export function librariesFolderOf(dataDir: string): string {
	const above = path.dirname(path.dirname(dataDir));
	if (fs.existsSync(path.join(above, LIBRARIES_MARK))) return above;
	return path.join(path.dirname(dataDir), LIBRARIES_FOLDER);
}

/**
 * The library chosen on the page to open when Sift starts, or null for "whichever opened last".
 *
 * Null as well when the chosen one has gone (a drive that is not connected, a library deleted since)
 * or the mark cannot be read: a start is never refused for a choice it cannot honour, it opens the
 * library that was open last, as it did before anybody chose.
 */
export function openingAtStart(dataDir: string): DataLocations | null {
	let written: unknown;
	try {
		written = JSON.parse(
			fs.readFileSync(path.join(librariesFolderOf(dataDir), LIBRARIES_MARK), 'utf8')
		);
	} catch {
		return null;
	}
	if (typeof written !== 'object' || written === null) return null;
	const chosen = (written as Record<string, unknown>)[OPENS_AT_START];
	if (typeof chosen !== 'object' || chosen === null) return null;
	const { data_dir: target, cache_dir: cache } = chosen as Record<string, unknown>;
	if (typeof target !== 'string' || typeof cache !== 'string') return null;
	if (!path.isAbsolute(target) || !path.isAbsolute(cache)) return null;
	if (!fs.existsSync(path.join(target, DATABASE_FILENAME))) return null;
	return { dataDir: target, cacheDir: cache };
}

/* Long enough for a cold start of the interpreter on a slow disk (it imports every slice to fill
 * the schema registry) and short enough that a folder on a network share that has gone away does
 * not leave somebody looking at a dialog that never appears. */
const ASK_TIMEOUT_MS = 60_000;

/** What a library folder turns out to be. The same five words the backend answers with. */
export type Verdict = 'empty' | 'current' | 'older' | 'newer' | 'unreadable' | 'unknown';

export interface LibraryReport {
	verdict: Verdict;
	/** A sentence for a person, when there is one. Empty otherwise. */
	detail: string;
}

/**
 * Ask the backend what is in a folder, without opening it.
 *
 * `unknown` when the question could not be put at all: no interpreter, a timeout, output that is
 * not the JSON it promised. It is a THIRD outcome rather than being folded into `unreadable`,
 * because the two mean opposite things to the caller: `unreadable` is a folder that is not a
 * library, and `unknown` is Sift failing to look. Offering to upgrade on the strength of a failed
 * look is exactly the mistake this whole path exists to avoid.
 */
export async function inspectLibrary(
	locations: DataLocations,
	run: Runner = runBackend
): Promise<LibraryReport> {
	return inspectPath(locations.dataDir, run);
}

/**
 * Ask the backend what is in one database FILE, without opening it or making anything from it.
 *
 * The same question and the same five verdicts as a folder, asked of a file somebody has just
 * chosen, so a file from a newer Sift is refused by the reading every other door uses.
 */
export async function inspectDatabase(
	file: string,
	run: Runner = runBackend
): Promise<LibraryReport> {
	return inspectPath(file, run);
}

async function inspectPath(target: string, run: Runner): Promise<LibraryReport> {
	const answer = await run([INSPECT_FLAG, target]);
	if (answer === null) return { verdict: 'unknown', detail: '' };
	const verdict = answer.verdict;
	if (
		verdict !== 'empty' &&
		verdict !== 'current' &&
		verdict !== 'older' &&
		verdict !== 'newer' &&
		verdict !== 'unreadable'
	) {
		/* A backend one version ahead could answer a word this shell has never heard of. "I could
		   not tell" is the honest reading of that, and it is the safe one. */
		return { verdict: 'unknown', detail: '' };
	}
	return {
		verdict,
		detail: typeof answer.detail === 'string' ? answer.detail : ''
	};
}

/**
 * Take a snapshot of a library's database beside it, and answer where it went.
 *
 * Null when it could not be made, and the caller must treat that as a refusal to upgrade rather
 * than as a detail. A one-way migration with no way back is the one thing this feature must never
 * do quietly.
 */
export async function backUpLibrary(
	locations: DataLocations,
	run: Runner = runBackend
): Promise<string | null> {
	const answer = await run([BACK_UP_FLAG, locations.dataDir]);
	if (answer === null || answer.ok !== true || typeof answer.copy !== 'string') return null;
	return answer.copy;
}

/**
 * Copy a database file into a new library's data folder as its `sift.sqlite3`, and answer where the
 * copy went.
 *
 * Null when it could not be made, which the caller reads as "nothing has been opened". The backend
 * does the copying, for the reason it does the backup: a WAL database can hold its newest rows in a
 * side file that a byte copy made here would leave behind.
 */
export async function adoptDatabase(
	file: string,
	locations: DataLocations,
	run: Runner = runBackend
): Promise<string | null> {
	const answer = await run([ADOPT_FLAG, file, locations.dataDir]);
	if (answer === null || answer.ok !== true || typeof answer.database !== 'string') return null;
	return answer.database;
}

/**
 * The library a backend asked to be started on, taken from the note it left, or null.
 *
 * WHY THE BACKEND ASKS AT ALL. A switch made from a browser is a request to the SERVER, which is on
 * whichever computer holds the library; only this process can start a backend on another folder,
 * so the server writes down which one and stops asking to be started again (exit 86). This reads
 * that note, when and only when the backend has stopped for that reason, and REMOVES it: a note
 * left behind would carry the next ordinary restart (the graphics card's) off to that library.
 *
 * What the server did before it wrote the note is the switch's checking: the library is on its own
 * list, it is not from a newer Sift, and an older one was agreed to and backed up. So what is
 * checked here is only that the note is one: two absolute folders, and nothing else.
 */
export function takeSwitchNote(dataDir: string): DataLocations | null {
	const note = path.join(dataDir, SWITCH_NOTE);
	let text: string;
	try {
		text = fs.readFileSync(note, 'utf8');
	} catch {
		return null;
	}
	try {
		fs.rmSync(note, { force: true });
	} catch {
		/* Read, and it will not go. The backend removes a note nothing acted on when it next starts
		   on this library, which is the one moment it could do harm. */
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		log.warning('library.switch_note_unreadable', {});
		return null;
	}
	if (typeof parsed !== 'object' || parsed === null) return null;
	const { data_dir: target, cache_dir: cache } = parsed as Record<string, unknown>;
	if (typeof target !== 'string' || typeof cache !== 'string') return null;
	if (!path.isAbsolute(target) || !path.isAbsolute(cache)) return null;
	return { dataDir: target, cacheDir: cache };
}

/** How one of those questions is put. Swapped in tests; there is no second implementation. */
export type Runner = (args: string[]) => Promise<Record<string, unknown> | null>;

/* The same interpreter and the same flags the backend itself is started with: `-I` so a stray
 * `pip install --user` on the machine cannot override what Sift shipped, `-u` so the answer is not
 * left sitting in a buffer. Reusing `INTERPRETER_ARGS` rather than repeating them is what keeps a
 * preflight from being run under a different Python from the one that will do the work. */
async function runBackend(args: string[]): Promise<Record<string, unknown> | null> {
	const { python } = bundlePaths();
	return new Promise((resolve) => {
		execFile(
			python,
			[...INTERPRETER_ARGS, '-m', 'sift.main', ...args],
			{ timeout: ASK_TIMEOUT_MS, windowsHide: true },
			(error, stdout) => {
				if (error) {
					log.warning('library.ask_failed', {
						args: args[0],
						reason: error.message
					});
					resolve(null);
					return;
				}
				try {
					const parsed: unknown = JSON.parse(lastLine(stdout));
					resolve(
						typeof parsed === 'object' && parsed !== null
							? (parsed as Record<string, unknown>)
							: null
					);
				} catch {
					/* Anything the interpreter printed before the answer (a deprecation warning from
					   a dependency, a line from a slice at import) would otherwise make a perfectly
					   good answer unparseable. The answer is the LAST line, by contract. */
					log.warning('library.ask_unreadable', { args: args[0] });
					resolve(null);
				}
			}
		);
	});
}

/** The last non-empty line of a program's output. */
function lastLine(text: string): string {
	const lines = text
		.split('\n')
		.map((one) => one.trim())
		.filter((one) => one !== '');
	return lines[lines.length - 1] ?? '';
}

/**
 * The list with this library at the front, opened just now.
 *
 * A PURE FUNCTION over the list, so what it does is a thing a test can state: one entry per data
 * folder, newest first, and a name that is never silently changed by a later open. The caller
 * writes what comes back.
 *
 * Called when a backend has STARTED on the folder, never when one was chosen. A path that was
 * picked and then refused must not become an entry offering to go back to it, and "started" is
 * not a call site being borrowed for a second purpose: starting a backend on a folder is what
 * opening a library IS.
 */
export function remembered(
	known: readonly KnownLibrary[],
	locations: DataLocations,
	when: number
): KnownLibrary[] {
	const existing = known.find((one) => sameFolder(one.dataDir, locations.dataDir));
	const entry: KnownLibrary = {
		dataDir: locations.dataDir,
		cacheDir: locations.cacheDir,
		name: existing?.name ?? nameFor(locations.dataDir),
		lastOpened: when
	};
	return [entry, ...known.filter((one) => !sameFolder(one.dataDir, locations.dataDir))];
}

/** Take one library off the list. The one that is open cannot be taken off; the caller checks. */
export function forgotten(known: readonly KnownLibrary[], dataDir: string): KnownLibrary[] {
	return known.filter((one) => !sameFolder(one.dataDir, dataDir));
}

/** The entry for a folder, or null when this copy has never opened it. */
export function find(known: readonly KnownLibrary[], dataDir: string): KnownLibrary | null {
	return known.find((one) => sameFolder(one.dataDir, dataDir)) ?? null;
}

/**
 * Whether two paths name the same folder.
 *
 * Case-insensitively, because Windows is: `C:\Sift\data` and `c:\sift\DATA` are one folder, and a
 * comparison that said otherwise would put the same library in the list twice and then offer to
 * switch to the one already open. Trailing separators go for the same reason.
 */
export function sameFolder(a: string, b: string): boolean {
	return normalise(a) === normalise(b);
}

function normalise(folder: string): string {
	return path.normalize(folder).replace(/[\\/]+$/, '').toLowerCase();
}

/**
 * What to call a library on screen.
 *
 * The folder HOLDING the data folder, because that is the one a person named: Sift puts `data` and
 * `cache` inside whatever was chosen, so every library in the list would otherwise be called
 * "data". The drive's own name where there is nothing above it.
 */
export function nameFor(dataDir: string): string {
	const holding = path.dirname(path.normalize(dataDir));
	const named = path.basename(holding);
	return named === '' ? holding : named;
}

/** The two folders for a library root somebody pointed at, the same pair first run makes. */
export function locationsUnder(root: string): DataLocations {
	return {
		dataDir: path.join(root, 'data'),
		cacheDir: path.join(root, 'cache')
	};
}

/**
 * What choosing one database file means, decided from its NAME and the list alone.
 *
 * - `library`: the file is a library's own `sift.sqlite3`, so the folder it sits in IS that
 *   library's data folder and the ordinary open runs on it.
 * - `adopt`: any other `.sqlite3`: a backup copy, a file somebody was given. It is not opened in
 *   place: a library folder is made BESIDE it, named after the file, the file is copied in as that
 *   library's database, and the copy is what opens. Opening in place would migrate the one file
 *   somebody pointed at, which for a backup copy is exactly the file they were keeping to go back to.
 * - `refused`: not a database file at all. The picker filters by extension, but a name can be typed.
 *
 * A PURE FUNCTION, so what each file becomes is a thing a test can state without a disk.
 */
export type DatabasePlan =
	| { kind: 'library'; locations: DataLocations }
	| {
			kind: 'adopt';
			source: string;
			stem: string;
			root: string;
			locations: DataLocations;
	  }
	| { kind: 'refused'; refusal: string };

export function planForDatabase(file: string, known: readonly KnownLibrary[]): DatabasePlan {
	const name = path.basename(file);
	if (path.extname(name).toLowerCase() !== '.sqlite3') {
		return {
			kind: 'refused',
			refusal: 'Choose a Sift database \u2014 its name ends in .sqlite3.'
		};
	}
	if (name.toLowerCase() === DATABASE_FILENAME) {
		const dataDir = path.dirname(file);
		/* The cache this copy used for it before, when it has opened it; otherwise where Sift itself
		 * puts one. */
		const cacheDir = find(known, dataDir)?.cacheDir ?? cacheBeside(dataDir);
		return { kind: 'library', locations: { dataDir, cacheDir } };
	}
	const stem = path.basename(name, path.extname(name));
	const root = path.join(path.dirname(file), stem);
	return {
		kind: 'adopt',
		source: file,
		stem,
		root,
		locations: locationsUnder(root)
	};
}

/**
 * The cache folder for a data folder this copy has no record of.
 *
 * Sift lays a library out as `<root>\data` and `<root>\cache`, so a data folder CALLED `data` has
 * its cache beside it under the same root, which is where the library's own previews already are.
 * Anything else is a database somebody keeps in a folder of their own choosing, and the root above
 * it could be a whole drive: a cache named after the folder, beside it, never spills into a folder
 * that is somebody else's.
 */
function cacheBeside(dataDir: string): string {
	const leaf = path.basename(dataDir);
	const holding = path.dirname(dataDir);
	if (leaf.toLowerCase() === 'data') return path.join(holding, 'cache');
	return path.join(holding, `${leaf}-cache`);
}

/**
 * Take back the folders an adoption made, when the copy into them failed.
 *
 * ONLY EMPTY ONES. `rmdirSync` refuses a folder with anything in it, which is the whole of what
 * makes this safe to call: a folder that somehow holds something is somebody's, and is left. Inner
 * folders first, so the root is empty by the time it is asked.
 */
export function unmakeEmpty(root: string, locations: DataLocations): void {
	for (const folder of [locations.dataDir, locations.cacheDir, root]) {
		try {
			fs.rmdirSync(folder);
		} catch {
			/* Not there, or not empty. Either way there is nothing of ours to take back. */
		}
	}
}
