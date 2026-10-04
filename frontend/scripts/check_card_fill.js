// Every card wears the card's light, and the shell's screen wears the page's.
//
// The rule and the two lists (the cards, and the flat grounds excused with their reasons) are in
// `lib/card-fill.js`, which the unit suite drives with planted lines
// (`src/lib/build/card-fill-gate.test.ts`). Not a ratchet: the count is zero and one is a failure.

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { CARDS, EXCUSED, unlitCardsIn, unlitScreenIn } from './lib/card-fill.js';
import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** A test harness may paint anything; it is a test, not a screen. */
const A_HARNESS = /\.test\.svelte$/;

const offenders = [];
const seen = new Set();

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (A_HARNESS.test(where)) continue;
	seen.add(where);
	const code = withoutComments(await readFile(path, 'utf8'));
	for (const { line, what } of unlitCardsIn(code, where))
		offenders.push(`${where}:${line}  ${what}`);
}

// A name on either list that no longer exists is a list nobody is reading.
for (const where of [...Object.keys(CARDS), ...Object.keys(EXCUSED)]) {
	if (!seen.has(where))
		offenders.push(`${where}  is named in lib/card-fill.js and is not in the tree`);
}

const layout = 'routes/+layout.svelte';
for (const what of unlitScreenIn(
	withoutComments(await readFile(join(SOURCE, 'routes', '+layout.svelte'), 'utf8'))
)) {
	offenders.push(`${layout}  ${what}`);
}

if (offenders.length > 0) {
	console.error(
		`\n  ${offenders.length} card ground(s) without the light:\n    ` +
			offenders.join('\n    ') +
			"\n  Instead: a card's ground is `var(--sift-card)` (or `--sift-card-fill` with no edge);" +
			' a ground that is not a card is excused in `scripts/lib/card-fill.js` with its reason.\n'
	);
	process.exit(1);
}

console.log(
	`Card fill: ${Object.keys(CARDS).length} cards wear the light, ` +
		`${Object.keys(EXCUSED).length} flat grounds are excused, and the screen wears the page's.`
);
