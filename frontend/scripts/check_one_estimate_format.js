// Every estimate of time still to go is worded by one function, `sayWindow` in `src/lib/shell/when.ts`.
//
// An estimate is a window in words ("about 30 to 45 minutes", "under an hour", "a few hours")
// and never a figure: what is ahead is not what was measured, and "40 minutes left" is read as a
// promise. Screens that word their own drift apart ("12m left", "About 40 minutes left"), and one
// will eventually say "0 minutes left" beside a bar that is not full.
//
// What this refuses, in code with its comments taken out (`lib/estimate-format.js`, which the unit
// suite drives with planted lines: `src/lib/build/estimate-format-gate.test.ts`): a number or an
// interpolation followed by a unit and "left", and "about" before an interpolation followed by a
// unit or by "to". Not a ratchet: there are none left, so one is a failure.

import { readFile } from 'node:fs/promises';

import { estimatesWrittenIn } from './lib/estimate-format.js';
import { everyFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

/** The one file allowed to word an estimate. */
const THE_FORMATTER = 'lib/shell/when.ts';

/** A test states what the formatter should say. */
const A_TEST = /\.(test|spec)\.ts$/;

const offenders = [];

for (const path of await everyFile(SOURCE, ['.ts', '.svelte', '.js'])) {
	const where = fromSource(path);
	if (where === THE_FORMATTER || A_TEST.test(where) || where.endsWith('.d.ts')) continue;
	for (const { line, what } of estimatesWrittenIn(withoutComments(await readFile(path, 'utf8')))) {
		offenders.push(`${where}:${line}  ${what}`);
	}
}

if (offenders.length > 0) {
	console.error(
		`\n  ${offenders.length} estimate(s) worded outside lib/shell/when.ts:\n    ` +
			offenders.join('\n    ') +
			'\n  Instead: `sayWindow(low, high)` from $lib/shell/when. A single figure is `sayWindow(s, s)`.\n'
	);
	process.exit(1);
}

console.log('One estimate format: every estimate is worded by lib/when.ts.');
