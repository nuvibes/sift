// A store or screen that reads a server subject, holds the answer, and is never told it moved.
//
// ## The failure this is for
//
// Almost everything Sift draws is read once and kept. The server says when what a screen drew has
// moved, on the one live connection (`lib/shell/live.svelte.ts`), and rings one of a handful of bells
// (`lib/library/changes.svelte.ts`): the library, arrivals, jobs, downloads, settings, this
// account's own lists, screens. A screen that listens re-reads; a screen that does not goes on
// showing the answer from before, in this tab and every other one, until somebody reloads the
// page. Nothing fails and nothing looks wrong: the read was right when it was made. The only tell
// is two windows disagreeing, which only a person comparing them by hand would notice.
//
// `check_settings_followed.js` holds the settings half of this. This holds the rest: a file that
// asks the server (`api.get`) and keeps the answer in state (`$state`) must name a bell, or say in
// its own text who follows for it, or why nothing on the server can move what it read.
//
// ## The marker, and what it is checked against
//
//     /* LIVE: followed by <path under src/> (<what that file re-reads, on which bell>) */
//     /* LIVE: nothing moves it (<why>) */
//
// A `followed by` names a file, and the file has to exist and has to name a bell itself: a marker
// pointing at a screen that listens to nothing is the same fault moved one file along. A
// `nothing moves it` is printed on every run, so the reasons stay in sight rather than becoming
// furniture.

import { readdir, readFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

/** A read of a server subject. */
const READS = /\bapi\.get\s*[<(]/;

/** Holding the answer, so it can go stale. A plain helper that hands a read back holds nothing. */
const HOLDS = /\$state\s*[<(]/;

/** The bells, and the two ways a file is told about settings. Any one of these is listening. */
const BELLS =
	/\b(libraryChanges|arrivals|jobChanges|downloadChanges|settingChanges|mine|screenChanges|onAssetStateChange|reloadOnLibraryChange|rereadOnHistoryChange|recorded|onSettingsSaved)\b/;

const MARKER = /LIVE:\s*(followed by|nothing moves it)\s*([^\n*]*)/;

/**
 * @param {string} dir
 * @returns {AsyncGenerator<string>}
 */
async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) yield* walk(full);
		else if (/\.(svelte|svelte\.ts)$/.test(entry.name)) yield full;
	}
}

/**
 * Whether a source names a bell, reading code only: a comment about a bell is not listening.
 * @param {string} text
 * @returns {boolean}
 */
export function listens(text) {
	const code = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
	return BELLS.test(code);
}

/**
 * What a file owes, or null. Factored out so a planted fault runs through exactly this check.
 * @param {string} text
 * @param {(path: string) => boolean} [exists]
 * @param {(path: string) => string} [read]
 * @returns {string | null}
 */
export function judge(
	text,
	exists = (path) => existsSync(join(SOURCE, path)),
	read = (path) => readFileSync(join(SOURCE, path), 'utf8')
) {
	if (!READS.test(text) || !HOLDS.test(text)) return null;
	if (listens(text)) return null;
	const said = text.match(MARKER);
	if (!said) return 'reads a server subject, holds it, and names no bell';
	if (said[1] === 'followed by') {
		const path = said[2].trim().split(/\s+/)[0] ?? '';
		if (!path || !exists(path))
			return `says it is followed by ${path || 'nothing'}, which is not a file`;
		if (!listens(read(path))) return `says it is followed by ${path}, which names no bell`;
	}
	return null;
}

const main = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];

if (main) {
	const offenders = [];
	const excused = [];
	for await (const full of walk(SOURCE)) {
		const where = relative(SOURCE, full).split('\\').join('/');
		if (/\.(test|spec)\./.test(where)) continue;
		const text = await readFile(full, 'utf8');
		const owed = judge(text);
		if (owed) {
			offenders.push(`${where}: ${owed}`);
			continue;
		}
		const said = text.match(MARKER);
		if (said && !listens(text)) excused.push(`${where}  ${said[1]} ${said[2].trim()}`);
	}
	if (offenders.length > 0) {
		console.error(
			`\n${offenders.length} file(s) read from the server, keep the answer, and are never told it moved.\n\n` +
				'  A screen showing one of these goes on showing the answer from before, in every other\n' +
				'  window, until somebody reloads the page. Re-read on the bell the server rings for it\n' +
				'  (`whenChanged(libraryChanges, ...)` in a screen, `<bell>.subscribe(...)` in a store\n' +
				'  that outlives every screen), or say who follows for it:\n\n' +
				'      /* LIVE: followed by <path under src/> (<what, on which bell>) */\n' +
				'      /* LIVE: nothing moves it (<why>) */\n'
		);
		for (const line of offenders) console.error(`    ${line}`);
		process.exit(1);
	}
	if (excused.length > 0) {
		console.log(`\n${excused.length} file(s) are followed by another, or read what nothing moves:`);
		for (const line of excused) console.log(`  ${line}`);
	}
	console.log('Live subjects: every reader that holds an answer is told when it moves.');
}
