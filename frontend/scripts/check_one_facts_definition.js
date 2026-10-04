#!/usr/bin/env node
/*
 * A file's facts are written down in ONE place.
 *
 * A size, a length, a frame rate, a shape, an encoder and a bit depth are drawn on several surfaces:
 * the record on the asset page, the stats panel over a clip or a still, the badge on a tile, the
 * duplicate-pair list in settings and a Theater cell. Separate copies of the arithmetic disagree: a
 * six-gigabyte file reads `5.6 GB` in one panel and `6.0 GB` two inches below it, `H.264` in one
 * and `h264` in the other, and every test passes because each number is right about itself.
 *
 * So this refuses the two things a second copy cannot be written without: a ladder of byte units,
 * and a table of encoder names. Neither has any other reason to appear in this codebase, and
 * `src/lib/library/facts.ts` is the one file allowed to hold them.
 *
 * Deliberately narrow. It does not try to recognise "formatting a size" in general: a gate that
 * guesses at intent produces arguments about whether it is right, and this one is answerable by
 * looking. Anything wanting these units or these names imports them.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(HERE, '..', 'src');

/** The one file that holds them. */
const ALLOWED = [resolve(SOURCE, 'lib', 'library', 'facts.ts')];

/*
 * Tests are not a second copy.
 *
 * A test that asserts a panel reads `H.264` is the evidence the one definition works, and refusing
 * it would mean the gate could only be satisfied by having no test of the thing it protects.
 */
const A_TEST = /\.(test|spec)\.(ts|js)$/;

const TELLS = [
	{
		what: 'a ladder of byte units',
		pattern: /(['"])(?:GB|TB)\1/,
		instead: "import { size } from '$lib/library/facts'"
	},
	{
		what: 'a table of encoder names',
		pattern: /(['"])H\.26[0-9]\1/,
		instead: "import { codec } from '$lib/library/facts'"
	}
];

function* walk(where) {
	for (const entry of readdirSync(where)) {
		const path = join(where, entry);
		if (statSync(path).isDirectory()) {
			yield* walk(path);
			continue;
		}
		if (path.endsWith('.ts') || path.endsWith('.svelte')) yield path;
	}
}

const found = [];
for (const path of walk(SOURCE)) {
	if (ALLOWED.includes(path) || A_TEST.test(path)) continue;
	const lines = readFileSync(path, 'utf8').split('\n');
	lines.forEach((line, index) => {
		for (const tell of TELLS) {
			if (tell.pattern.test(line)) {
				found.push({ where: `${relative(SOURCE, path)}:${index + 1}`, tell, line: line.trim() });
			}
		}
	});
}

if (found.length > 0) {
	console.error(`\nA file's facts are worked out in more than one place:\n`);
	for (const one of found) {
		console.error(`  ${one.where}`);
		console.error(`    ${one.line}`);
		console.error(`    ^ ${one.tell.what}. Use ${one.tell.instead} instead.\n`);
	}
	console.error(
		'src/lib/library/facts.ts is where a size, a length, a rate, a shape, an encoder and a bit depth\n' +
			'are written down. A second copy is not a second opinion: it is the same fact spelled two\n' +
			'ways on two surfaces of one page, and nothing but reading the page will find it.\n'
	);
	process.exit(1);
}

console.log(`one facts definition: clean (${ALLOWED.length} file holds them)`);
