// Every empty state says what the empty thing is, so the look follows from a fact about the screen
// rather than from a flag somebody remembered to set.
//
// `scope="page"`: the empty thing IS the screen (a wall, a tab, a list that fills the page), drawn
// as the glyph, one line in the display face, one sentence and the one action. `scope="block"`: a
// list or a band inside a page that is otherwise full, drawn as the sentence alone. What this
// refuses, in code with its comments taken out (`lib/empty-scope.js`, which the unit suite drives
// with planted lines: `src/lib/build/empty-scope-gate.test.ts`): an `Empty` naming no scope, and the
// older `quiet` flag. Not a ratchet: one is a failure.

import { readFile } from 'node:fs/promises';

import { unscopedEmptiesIn } from './lib/empty-scope.js';
import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** A test harness mounts `Empty` to drive it; that is a test using it, not a screen. */
const A_HARNESS = /\.test\.svelte$/;

const offenders = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (A_HARNESS.test(where)) continue;
	for (const { line, what } of unscopedEmptiesIn(withoutComments(await readFile(path, 'utf8')))) {
		offenders.push(`${where}:${line}  ${what}`);
	}
}

if (offenders.length > 0) {
	console.error(
		`\n  ${offenders.length} empty state(s) that do not say their scope:\n    ` +
			offenders.join('\n    ') +
			'\n  Instead: scope="page" where the empty thing is the screen (glyph, display line,' +
			' sentence, the one action), scope="block" where it sits inside a page (the sentence alone).\n'
	);
	process.exit(1);
}

console.log('Empty scope: every empty state says whether it is a page or a block.');
