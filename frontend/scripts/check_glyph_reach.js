// A rule that colours a glyph names it by a child combinator, so it cannot reach into a button.
//
// A glyph inside a button wears the button's ink. A row that tints its own mark with
// `.list :global(.icon)` tints the glyph of every button in the row as well, and the button
// cannot win that back: the two selectors weigh the same and load order decides. The rule and
// the tolerated list are in `lib/glyph-reach.js`.
//
// Run: node scripts/check_glyph_reach.js

import { readFile } from 'node:fs/promises';

import { againstKnownGlyphs, glyphReaches } from './lib/glyph-reach.js';
import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

const files = await everySvelteFile(SOURCE);
const sources = await Promise.all(
	files.map(async (file) => ({
		file: fromSource(file),
		text: withoutComments(await readFile(file, 'utf8'))
	}))
);
const { fresh, stale } = againstKnownGlyphs(glyphReaches(sources));

if (fresh.length > 0) {
	console.error(
		'These rules colour every glyph under an element, so a button placed inside it has its glyph\n' +
			"tinted too, and a glyph in a button wears the button's own ink:\n"
	);
	for (const one of fresh) console.error(`  ${one.file}\n    ${one.selector}`);
	console.error(
		'\nName the glyph you mean by a child combinator: `.list > li > :global(.icon)`, not\n' +
			'`.list :global(.icon)`.'
	);
	process.exit(1);
}

if (stale.length > 0) {
	console.error('These are listed as reaching a glyph and no longer do. Take them off the list:\n');
	for (const one of stale) console.error(`  ${one}`);
	process.exit(1);
}

console.log(`glyph-reach: ${files.length} components checked; no rule tints a button's glyph.`);
