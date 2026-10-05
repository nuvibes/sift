// How long the client and the desktop shell are, how their functions branch, and how much of them
// is comment: ratchets that may only fall.
//
// Every `.svelte` and `.ts` module over 1,000 lines, every function in one whose cyclomatic
// complexity is over 20, every test file over 2,000, every module or
// test file of fifty or more non-blank lines whose comments are over a quarter of them, and the count of loose
// files at the top of `src/lib`, recorded under `code-shape` in `gate-baselines.json`. A number
// that grew is refused, so is a new entry over the line, and a fall is recorded with `--record`.
// A component under `src/lib/components` that imports a file at the top of `src/lib` is refused.
// The measuring and the comparison are in `lib/code-shape.js`, driven by
// `src/lib/build/code-shape.test.ts`; the Python is held by `scripts/check_code_shape.py`.
//
// The gallery under `routes/design/` is a separate repository absent from a clone, so not read.

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

import { addFile, asRecord, compareShape, emptyShape, looseImports } from './lib/code-shape.js';
import { BASELINES, everyFile, FRONTEND, RATCHET_FELL, recording, SOURCE } from './lib/tree.js';

const REPO = join(FRONTEND, '..');
const GATE = 'code-shape';
const PLANTED = 'GateFixture';
const A_TEST = /\.(test|spec)\.(ts|svelte)$/;
/** Far below the real count: it catches a walk that has stopped finding files. */
const AT_LEAST = 1000;

const trees = [
	{ dir: SOURCE, extensions: ['.svelte', '.ts'], tests: false },
	{ dir: join(FRONTEND, 'e2e'), extensions: ['.ts'], tests: true },
	{ dir: join(REPO, 'desktop', 'src'), extensions: ['.ts'], tests: false }
];

const COMPONENTS = 'frontend/src/lib/components/';

const atTop = await readdir(join(SOURCE, 'lib'), { withFileTypes: true });
const folders = new Set(atTop.filter((entry) => entry.isDirectory()).map((entry) => entry.name));

const shape = emptyShape();
/** @type {string[]} */
const loose = [];
let read = 0;
for (const { dir, extensions, tests } of trees) {
	for (const path of await everyFile(dir, extensions)) {
		const where = relative(REPO, path).replaceAll('\\', '/');
		const name = where.split('/').pop() ?? '';
		if (where.startsWith('frontend/src/routes/design/') || name.endsWith('.d.ts')) continue;
		if (name.startsWith(PLANTED)) continue;
		const text = await readFile(path, 'utf8');
		addFile(shape, where, text, tests || A_TEST.test(name));
		if (where.startsWith(COMPONENTS))
			for (const specifier of looseImports(where.slice('frontend/src/lib/'.length), text, folders))
				loose.push(`${where}: ${specifier}`);
		read += 1;
	}
}

const libTopLevel = atTop.filter(
	(entry) => entry.isFile() && !entry.name.startsWith(PLANTED)
).length;

if (read < AT_LEAST) {
	console.error(`code-shape: only ${read} files read; the walk has stopped finding them.`);
	process.exit(1);
}

if (loose.length > 0) {
	console.error(
		'\ncode-shape: a component imports a file at the top of src/lib. Every module there lives in' +
			' the folder for its area; import it from there:\n  ' +
			loose.join('\n  ')
	);
	process.exit(1);
}

const all = JSON.parse(await readFile(BASELINES, 'utf8'));
/* The first record is today's tree whole; after it, only falls are written. */
if (!(GATE in all)) {
	if (!recording()) {
		console.error('code-shape: nothing recorded. Run: node scripts/check_code_shape.js --record');
		process.exit(1);
	}
	all[GATE] = asRecord(shape, libTopLevel);
	await writeFile(BASELINES, `${JSON.stringify(all, null, '\t')}\n`, 'utf8');
	console.log(`code-shape: first record written to scripts/gate-baselines.json.`);
	process.exit(0);
}
const { rose, added, fell } = compareShape(all[GATE] ?? {}, shape, libTopLevel);

if (rose.length > 0 || added.length > 0) {
	console.error(
		'\ncode-shape: over its line and past what is recorded, or new and over the line. Shorten it,' +
			' or move it into a folder:\n  ' +
			[...rose, ...added].join('\n  ')
	);
	process.exit(1);
}

if (fell.length > 0) {
	if (!recording()) {
		console.error(
			`\n${RATCHET_FELL} code-shape: record it so the progress cannot be given back:` +
				' node scripts/check_code_shape.js --record\n  ' +
				fell.join('\n  ')
		);
		process.exit(1);
	}
	all[GATE] = asRecord(shape, libTopLevel);
	await writeFile(BASELINES, `${JSON.stringify(all, null, '\t')}\n`, 'utf8');
	console.log(`code-shape: recorded ${fell.length} change(s) in scripts/gate-baselines.json.`);
}
