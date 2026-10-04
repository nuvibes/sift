// One veil, one drop offer, one withheld face, each drawn by its own component.
//
// The rule is in `lib/one-veil.js`, which the unit suite drives with planted lines
// (`src/lib/build/one-veil-gate.test.ts`). Not a ratchet: the count is zero and one is a failure.

import { readFile } from 'node:fs/promises';

import { handDrawnVeilsIn } from './lib/one-veil.js';
import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** A test harness may mount anything; it is a test, not a screen. */
const A_HARNESS = /\.test\.svelte$/;

const offenders = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (A_HARNESS.test(where)) continue;
	for (const { line, what } of handDrawnVeilsIn(
		withoutComments(await readFile(path, 'utf8')),
		where
	)) {
		offenders.push(`${where}:${line}  ${what}`);
	}
}

if (offenders.length > 0) {
	console.error(
		`\n  ${offenders.length} hand-drawn veil(s):\n    ` +
			offenders.join('\n    ') +
			'\n  Instead: a dialog is `Modal`; a panel that is not a dialog puts `Veil` behind it;' +
			' a whole-window drop is `DropOffer`; a Hidden face is `Withheld`.\n'
	);
	process.exit(1);
}

console.log(
	'One veil: every dimmed sheet, drop offer and withheld face is drawn by its component.'
);
