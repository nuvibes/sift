// Every screen says what it draws, and listens for each bell the server rings when that moves.
//
// `check_live_subjects.js` asks one file at a time whether it holds an answer and names a bell.
// This asks the SCREEN: the matrix in `src/lib/library/live-matrix.ts` names, for every route and
// every Settings section, the things it draws, and each thing has the bells the server rings when
// it moves (`THING_BELLS`). A screen that draws people and never hears the library goes on showing
// a renamed person until somebody reloads the page, while every file in it passes the other check.
//
// It fails when a route or a section has no row, when a row names a file that is not there, when a
// bell a row's things need is heard nowhere in the screen's own code or the modules it imports,
// and when a row whose membership hangs on opinions (Favorites) re-reads nothing on one. A row may
// say what it still `owes`: printed on every run, so it stays in sight.

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { everyFile, SOURCE, withoutComments } from './lib/tree.js';

const MATRIX = join(SOURCE, 'lib', 'library', 'live-matrix.ts');
const SECTIONS = join(SOURCE, 'lib', 'settings-ui', 'sections.ts');

/**
 * A bell heard: a re-read wired to it, never a mention (ringing one, or importing it, is not).
 * @param {string} bell
 */
function hearing(bell) {
	return `whenChanged\\(\\s*${bell}\\b|\\b${bell}\\.(subscribe\\(|generation\\b)`;
}

/** How each bell is heard in code. */
const HEARD = {
	libraryChanges: new RegExp(
		`${hearing('libraryChanges')}|\\b(reloadOnLibraryChange|rereadOnHistoryChange)\\(`
	),
	arrivals: new RegExp(hearing('arrivals')),
	settled: new RegExp(
		`${hearing('arrivals')}|\\b(reloadOnFilesSettled|reloadOnLibraryChange)\\(|\\bimports\\.settled\\b`
	),
	assetState: new RegExp(`${hearing('assetState')}|\\bonAssetStateChange\\(`),
	mine: new RegExp(`${hearing('mine')}|\\breloadOnLibraryChange\\(`),
	jobChanges: new RegExp(hearing('jobChanges')),
	downloadChanges: new RegExp(hearing('downloadChanges')),
	settingChanges: new RegExp(
		`${hearing('settingChanges')}|\\bonSettingsSaved\\(|new SettingsPanel\\(`
	),
	screenChanges: new RegExp(hearing('screenChanges'))
};

/** How far a `via` file may sit from the screen's own file, in imports. */
const DEPTH = 4;

/**
 * An array literal from the matrix file, read as data. The file is TypeScript; the literal is not.
 * @param {string} text
 * @param {string} name
 * @returns {any}
 */
export function literal(text, name) {
	const start = text.indexOf(`export const ${name}`);
	if (start < 0) throw new Error(`live-matrix.ts has no ${name}`);
	const open = text.indexOf('= ', start) + 2;
	const close = text.indexOf(text[open] === '[' ? '\n];' : '\n};', open) + 2;
	return new Function(`return (${withoutComments(text.slice(open, close))})`)();
}

/**
 * The code of a source file with comments and every ringing of a bell taken out.
 * @param {string} text
 */
export function heardIn(text) {
	return withoutComments(text).replace(/\b\w+\.changed\(\)/g, '');
}

/** @type {Map<string, string[]>} */
const importCache = new Map();

/**
 * The files a module imports from `$lib` or by a relative path.
 * @param {string} from path under src/
 * @param {string} text
 * @returns {string[]}
 */
export function importsOf(from, text) {
	const key = `${from}\n${text.length}`;
	const known = importCache.get(key);
	if (known) return known;
	/** @type {string[]} */
	const found = [];
	for (const match of text.matchAll(/(?:from|import)\s*\(?\s*'([^']+)'/g)) {
		const spec = match[1];
		let base;
		if (spec.startsWith('$lib/')) base = 'lib/' + spec.slice(5);
		else if (spec.startsWith('.')) base = join(dirname(from), spec).split('\\').join('/');
		else continue;
		for (const tail of ['', '.ts', '.svelte.ts', '.js', '/index.ts']) {
			if (/\.(svelte|ts|js)$/.test(base + tail) && existsSync(join(SOURCE, base + tail))) {
				found.push(base + tail);
				break;
			}
		}
	}
	importCache.set(key, found);
	return found;
}

/** @type {Map<string, string>} */
const cache = new Map();

/** @param {string} path under src/ */
function readOnce(path) {
	if (!cache.has(path)) cache.set(path, readFileSync(join(SOURCE, path), 'utf8'));
	return /** @type {string} */ (cache.get(path));
}

/**
 * The files a screen reaches through its imports, to `DEPTH`.
 * @param {string} file path under src/
 * @param {(path: string) => string} read
 */
export function reachedFrom(file, read = readOnce) {
	const seen = new Set([file]);
	let wave = [file];
	for (let depth = 0; depth < DEPTH && wave.length > 0; depth += 1) {
		const next = [];
		for (const path of wave) {
			for (const one of importsOf(path, read(path))) {
				if (seen.has(one) || /\.(test|spec)\./.test(one)) continue;
				seen.add(one);
				next.push(one);
			}
		}
		wave = next;
	}
	return seen;
}

/**
 * Every bell a screen hears: in its own file and in the `via` files that keep it (a wall's grid,
 * its catch-up), never in anything else it happens to import. A dialog or a store it imports hears
 * for itself, not for the screen.
 * @param {string} file path under src/
 * @param {string[]} via
 * @param {(path: string) => string} read
 */
export function bellsOf(file, via = [], read = readOnce) {
	const heard = new Set();
	let rereads = false;
	for (const path of [file, ...via]) {
		const code = heardIn(read(path));
		for (const [bell, pattern] of Object.entries(HEARD)) if (pattern.test(code)) heard.add(bell);
		if (rereadsOnOpinion(code)) rereads = true;
	}
	return { heard, rereadsOnOpinion: rereads };
}

/**
 * Whether an opinion arriving re-reads the list, not only restyles a row it already holds.
 * @param {string} code
 */
export function rereadsOnOpinion(code) {
	for (const match of code.matchAll(/onAssetStateChange\(/g)) {
		const body = code.slice(match.index, match.index + 1200).split(/\n\t\}\);/)[0];
		if (/\b(catchUp|opinionMoved|reread\w*|reload\w*|load)\s*\(/.test(body)) return true;
	}
	return false;
}

/**
 * What one row owes, or null.
 * @param {{ screen: string, file: string, via?: string[], shows: string[], membership?: string, owes?: string[] }} row
 * @param {Record<string, string[]>} thingBells
 * @param {(file: string, via: string[]) => { heard: Set<string>, rereadsOnOpinion: boolean }} [bells]
 * @param {(file: string) => Set<string>} [reach]
 */
export function judgeRow(row, thingBells, bells = bellsOf, reach = reachedFrom) {
	for (const path of [row.file, ...(row.via ?? [])])
		if (!existsSync(join(SOURCE, path))) return `names ${path}, which is not a file`;
	if (row.via?.length) {
		const reached = reach(row.file);
		const stray = row.via.filter((path) => !reached.has(path));
		if (stray.length > 0) return `says it is kept by ${stray.join(', ')}, which it does not import`;
	}
	const { heard, rereadsOnOpinion: rereads } = bells(row.file, row.via ?? []);
	const owed = new Set(row.owes ?? []);
	const missing = [];
	for (const thing of row.shows) {
		if (!(thing in thingBells)) return `shows ${thing}, which THING_BELLS does not name`;
		if (owed.has(thing)) continue;
		for (const bell of thingBells[thing])
			if (!heard.has(bell)) missing.push(`${thing} needs ${bell}`);
	}
	if (missing.length > 0) return `never hears ${[...new Set(missing)].join(', ')}`;
	if (row.membership === 'opinions' && !rereads)
		return 'hangs on opinions and re-reads nothing on one (a heart set elsewhere never joins it)';
	return null;
}

/** Every screen that must have a row: each route, and each Settings section. */
async function screens() {
	// The design gallery is not in the public tree: its pages draw fixed examples and follow no bell.
	const routes = (await everyFile(join(SOURCE, 'routes'), ['+page.svelte']))
		.map((full) => relative(SOURCE, full).split('\\').join('/'))
		.filter((screen) => !screen.startsWith('routes/design/'));
	const sections = [...readFileSync(SECTIONS, 'utf8').matchAll(/\{ id: '([^']+)', label:/g)].map(
		(match) => `settings/${match[1]}`
	);
	return [...routes, ...sections];
}

const main = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];

if (main) {
	const text = readFileSync(MATRIX, 'utf8');
	const thingBells = literal(text, 'THING_BELLS');
	/** @type {{ screen: string, file: string, via?: string[], shows: string[], owes?: string[], why?: string }[]} */
	const rows = literal(text, 'LIVE_MATRIX');
	const named = new Map(rows.map((row) => [row.screen, row]));
	const problems = [];
	for (const screen of await screens()) {
		if (!named.has(screen)) problems.push(`${screen}: no row in the live matrix`);
	}
	const owing = [];
	for (const row of rows) {
		const owed = judgeRow(row, thingBells);
		if (owed) problems.push(`${row.screen}: ${owed}`);
		if (row.owes?.length)
			owing.push(`${row.screen}  owes ${row.owes.join(', ')}: ${row.why ?? ''}`);
		if (process.argv.includes('--show')) {
			const { heard } = bellsOf(row.file, row.via ?? []);
			console.log(`${row.screen}  hears ${[...heard].sort().join(' ')}`);
		}
	}
	if (problems.length > 0) {
		console.error(
			`\n${problems.length} screen(s) draw something they are never told has moved.\n\n` +
				'  Each is a screen that goes on showing the answer from before in every other window\n' +
				'  until somebody reloads the page. Listen for the bell (`src/lib/library/changes.svelte.ts`),\n' +
				'  or give the screen its row in `src/lib/library/live-matrix.ts`.\n'
		);
		for (const line of problems) console.error(`    ${line}`);
		process.exit(1);
	}
	if (owing.length > 0) {
		console.log(`\n${owing.length} screen(s) still owe a bell:`);
		for (const line of owing) console.log(`  ${line}`);
	}
	console.log(`Live matrix: ${rows.length} screens each hear every bell for what they draw.`);
}
