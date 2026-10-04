// Every button wears what its sort says: an act its glyph and words, a navigation or an answer its
// words, a glyph alone only under a tooltip.
//
// The rule is at the head of `lib/components/common/Button.svelte`; what is refused is in
// `lib/button-glyphs.js`, which the unit suite drives with planted markup
// (`src/lib/build/button-glyphs-gate.test.ts`). Refused: an icon-only button with no `Tooltip` around
// it (bar the named few in `UNLABELLED`, which can only shrink), an act named in plain words with no
// glyph or with another act's glyph, and an answer or a word-tone button wearing a glyph. Counted as
// a ratchet: a navigation wearing a glyph that is not a direction mark.
//
// The gallery under `routes/design/` is not read: it draws controls for comparison on purpose, and
// it is a separate repository absent from a clone.

import { readFile } from 'node:fs/promises';

import { buttonFaultsIn, UNLABELLED } from './lib/button-glyphs.js';
import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

const DRAWN_ON_PURPOSE = 'routes/design/';

/** A test plants what it asserts about. */
const A_TEST = /\.test\.svelte$/;

/** Far below the real count: it catches a walk or a reader that has stopped finding buttons. */
const AT_LEAST = 200;

const faults = [];
const navigations = [];
/** @type {Set<string>} */
const excused = new Set();
let read = 0;

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where.startsWith(DRAWN_ON_PURPOSE) || A_TEST.test(where)) continue;
	const code = withoutComments(await readFile(path, 'utf8'));
	read += (code.match(/<(Button|SplitButton)\b/g) ?? []).length;
	const found = buttonFaultsIn(code, where, excused);
	for (const { line, what } of found.faults) faults.push(`${where}:${line}  ${what}`);
	for (const { line, what } of found.glyphedNavigations)
		navigations.push(`${where}:${line}  ${what}`);
}

let failed = false;

if (read < AT_LEAST) {
	console.error(
		`\n  Only ${read} buttons were read, and there are several hundred. The walk or the reader has` +
			' stopped finding them, so a clean report here would mean nothing.\n'
	);
	failed = true;
}

if (faults.length > 0) {
	console.error(
		`\n  ${faults.length} button(s) wearing the wrong thing:\n    ` +
			faults.join('\n    ') +
			'\n  The rule is at the head of lib/components/common/Button.svelte: an act wears its glyph' +
			' and its words, a navigation or an answer its words, and a glyph alone sits inside a' +
			' Tooltip naming it.\n'
	);
	failed = true;
}

const stale = [...UNLABELLED.keys()].filter((key) => !excused.has(key));
if (stale.length > 0) {
	console.error(
		`\n  UNLABELLED names icon-only buttons that are no longer there:\n    ${stale.join('\n    ')}` +
			'\n  Take them out of lib/button-glyphs.js, so the list says only what is true.\n'
	);
	failed = true;
}

const complaint = await ratchet('button-glyphs', 'glyphedNavigations', navigations.length, {
	what: 'navigations wearing a glyph that is not a direction mark',
	instead:
		'A button that goes somewhere wears its words, and at most an arrow on the side it goes.',
	offenders: navigations
});
if (complaint) {
	console.error(`\n  ${complaint}\n`);
	failed = true;
}

if (failed) process.exit(1);

console.log(
	`Button glyphs: ${read} buttons read; every act wears its glyph, every icon-only one a tooltip.`
);
