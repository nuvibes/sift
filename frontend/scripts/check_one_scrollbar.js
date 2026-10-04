// One scrollbar, drawn in one place, and the count of screens still painting their own may only
// fall.
//
// ## What this is for
//
// Every scrolling region in Sift goes through `Scroller`, which keeps the browser's own scrolling
// and replaces only the bar. That matters for two reasons and only one of them is looks.
//
// The looks half: a painted scrollbar and a floating one are not the same width. The painted one
// takes ten pixels of LAYOUT, so a justified wall measured against a box with one gets ten pixels
// less than it thought, and the region beside it that floats its bar does not, which is two
// screens that should be identical disagreeing about where their right edge is.
//
// The other half is worse. A `max-block-size` with no `overflow` beside it does not scroll, it
// CLIPS, and the two are indistinguishable in a stylesheet. Every file declaring its own pairing of
// the two is a place where somebody could later remove one line and silently turn a scrolling list
// into a truncated one.
//
// ## Why a ratchet and not a ban
//
// The same reason `check_handrolled.js` is one: a gate that fails on dozens of files the day it is
// written gets weakened until it passes, and a weakened gate is worse than none because it reports
// success.
//
// It is at one. The one is a comment in `AssetGrid` explaining why a scrolling box clips on both
// axes: prose about the trap, which is worth keeping. It is counted rather than exempted by name,
// because an exemption list is a second thing to maintain and this number is already the honest
// one.
//
// ## What counts
//
// A declaration, not a mention. The check reads the STYLE block only, so a comment anywhere in the
// file is invisible to it.

import { readFile } from 'node:fs/promises';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/** The component that IS the scrolling region. It declares the real thing, on purpose. */
const THE_SCROLLER = 'lib/components/common/Scroller.svelte';

/**
 * A screen declaring its own scrolling or its own scrollbar.
 *
 * `overflow: hidden` is deliberately not here: hiding overflow is a clip somebody meant, and it is
 * how a flex column stops its content escaping. What this is about is a box that scrolls.
 *
 * Not anchored to the start of a line: `^\s*overflow...` matches every declaration prettier writes
 * and misses `.btn { overflow-y: auto; }` entirely. Comments are stripped before this runs and the
 * script block is never read, so dropping the anchor cannot match prose.
 */
const OWN_SCROLL =
	/overflow(-x|-y)?\s*:\s*(auto|scroll)|scrollbar-width\s*:|scrollbar-color\s*:|::-webkit-scrollbar/g;

/** The style block, with its comments stripped so prose about scrolling is not a declaration. */
function stylesOf(body) {
	const opens = body.indexOf('<style>');
	if (opens === -1) return '';
	// Block comments only: a `//` in a stylesheet is not a comment, and there is no markup here.
	return withoutComments(body.slice(opens, body.lastIndexOf('</style>')), {
		markup: false,
		line: false
	});
}

const offenders = [];
let total = 0;

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where === THE_SCROLLER) continue;

	const found = stylesOf(await readFile(path, 'utf8')).match(OWN_SCROLL)?.length ?? 0;
	if (found === 0) continue;
	total += found;
	offenders.push({ where, found });
}

/* A scan that found nothing would pass forever. There are hundreds of components; if this walk
   finds a handful, the folder moved and the gate is reading an empty room. */
const scanned = (await everySvelteFile(SOURCE)).length;
if (scanned < 100) {
	console.error(
		`\none-scrollbar: only ${scanned} components found under src/.\n` +
			`  That is too few to be right: the tree moved, or this gate is reading the wrong one.\n`
	);
	process.exit(1);
}

const complaint = await ratchet('one-scrollbar', 'ownScroll', total, {
	what: 'screens declaring their own scrolling',
	instead:
		`Wrap the content in Scroller instead: it keeps the browser's own scrolling and replaces\n` +
		`    only the bar, so the region looks and measures the same as every other one.\n` +
		`    Put the ceiling on the box that SCROLLS, not on the content: a max-block-size with no\n` +
		`    overflow beside it does not scroll, it CLIPS, and the two look identical in a stylesheet.`,
	offenders: offenders.sort((a, b) => b.found - a.found).map((one) => `${one.found}x  ${one.where}`)
});

if (complaint) {
	console.error(`\nOne scrollbar, drawn in one place.\n`);
	console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(`one-scrollbar: ${total} screens declaring their own scroll (at the recorded number)`);
