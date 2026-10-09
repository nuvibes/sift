/* The libraries this copy has opened, and what has to be true before it opens another. */

import { execFile } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { INTERPRETER_ARGS } from './backend';
import { log } from './log';
import { bundlePaths, type DataLocations } from './paths';
import type { KnownLibrary } from './settings';

/* The questions the backend answers without starting. */
export const INSPECT_FLAG = '--inspect-library';
export const BACK_UP_FLAG = '--back-up-library';
export const ADOPT_FLAG = '--adopt-library';

/** The name a library's own database has inside its data folder. `DATABASE_FILENAME` in db.py. */
export const DATABASE_FILENAME = 'sift.sqlite3';

/* The note a backend leaves in its own data folder, naming the library to start on, before it
 * stops asking to be started again. */
export const SWITCH_NOTE = 'library-switch.json';

/* The libraries folder's own mark, the folder's name beside a first library, and the key in the
 * mark naming the library chosen to open when Sift starts. */
export const LIBRARIES_MARK = 'sift-libraries.json';
export const LIBRARIES_FOLDER = 'libraries';
export const OPENS_AT_START = 'opens_at_start';

/** The folder libraries are made in, found from a library's data folder the way the backend finds
 * it (`libraries_folder` in sift/kernel/config.py): the folder two above a member's data folder
 * when it carries the mark, and `libraries` beside the data folder otherwise. */
export function librariesFolderOf(dataDir: string): string {
	const above = path.dirname(path.dirname(dataDir));
	if (fs.existsSync(path.join(above, LIBRARIES_MARK))) return above;
	return path.join(path.dirname(dataDir), LIBRARIES_FOLDER);
}

export function holdsLibrary(dataDir: string): boolean {
	return fs.existsSync(path.join(dataDir, DATABASE_FILENAME));
}

/** The library chosen on the page to open when Sift starts, or null for "whichever opened last". */
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
	if (!holdsLibrary(target)) return null;
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

/** Ask the backend what is in a folder, without opening it. */
export async function inspectLibrary(
	locations: DataLocations,
	run: Runner = runBackend
): Promise<LibraryReport> {
	return inspectPath(locations.dataDir, run);
}

/** Ask the backend what is in one database FILE, without opening it or making anything from it. */
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
		/* A backend one version ahead could answer a word this shell has never heard of. */
		return { verdict: 'unknown', detail: '' };
	}
	return {
		verdict,
		detail: typeof answer.detail === 'string' ? answer.detail : ''
	};
}

/** Take a snapshot of a library's database beside it, and answer where it went. */
export async function backUpLibrary(
	locations: DataLocations,
	run: Runner = runBackend
): Promise<string | null> {
	const answer = await run([BACK_UP_FLAG, locations.dataDir]);
	if (answer === null || answer.ok !== true || typeof answer.copy !== 'string') return null;
	return answer.copy;
}

/** Copy a database file into a new library's data folder as its `sift.sqlite3`, and answer where
 * the copy went. */
export async function adoptDatabase(
	file: string,
	locations: DataLocations,
	run: Runner = runBackend
): Promise<string | null> {
	const answer = await run([ADOPT_FLAG, file, locations.dataDir]);
	if (answer === null || answer.ok !== true || typeof answer.database !== 'string') return null;
	return answer.database;
}

/** The library a backend asked to be started on, taken from the note it left, or null. */
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
		/* Read, and it will not go. The backend removes a note nothing acted on when it next
		   starts on this library, which is the one moment it could do harm. */
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
 * `pip install --user` on the machine cannot override what Sift shipped, `-u` so the answer is
 * not left sitting in a buffer. */
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
					/* Anything the interpreter printed before the answer (a deprecation warning
					   from a dependency, a line from a slice at import) would otherwise make a
					   perfectly good answer unparseable. */
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

/** The list with this library at the front, opened just now. */
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

/** Whether two paths name the same folder. */
export function sameFolder(a: string, b: string): boolean {
	return normalise(a) === normalise(b);
}

function normalise(folder: string): string {
	return path
		.normalize(folder)
		.replace(/[\\/]+$/, '')
		.toLowerCase();
}

/** What to call a library on screen. */
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

/** What choosing one database file means, decided from its NAME and the list alone. */
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

/** The cache folder for a data folder this copy has no record of. */
function cacheBeside(dataDir: string): string {
	const leaf = path.basename(dataDir);
	const holding = path.dirname(dataDir);
	if (leaf.toLowerCase() === 'data') return path.join(holding, 'cache');
	return path.join(holding, `${leaf}-cache`);
}

/** Take back the folders an adoption made, when the copy into them failed. */
export function unmakeEmpty(root: string, locations: DataLocations): void {
	for (const folder of [locations.dataDir, locations.cacheDir, root]) {
		try {
			fs.rmdirSync(folder);
		} catch {
			/* Not there, or not empty. Either way there is nothing of ours to take back. */
		}
	}
}
