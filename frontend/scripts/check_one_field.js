// A text field looks the same everywhere, because one rule says what it looks like.
//
// ## What this counts
//
// A style rule aimed at a field (an `input`, a `textarea`, a `.input`) that restates the box
// `app.css` already gives every field in the application: the height, the padding, the edge, the
// corner, the ground and the face. Three or more of those six in one rule is a copy, whether it
// agrees with the original or quietly differs from it. Copies drift: a heavier edge here, a smaller
// corner there, and sibling walls whose search boxes stop matching.
//
// ## Why this is a ratchet and not a ban
//
// The same reason `check_handrolled.js` gives. The ones left each have a local reason a gate cannot
// read: a field on a different ground, a shorter one in the top bar, one with no box at all inside
// a chrome frame, a label standing in for the file input `app.css` deliberately excludes. Refusing
// them would mean either flattening real differences or weakening the gate until it passed. So it
// counts, and the count may fall and may never rise.
//
// ## The one thing this is NOT
//
// It is not a claim that `app.css` has the right values, only that there is one set of them: h36,
// `--sift-surface-3`, 1px `--sift-line`, `--radius-md`.

import { readFile } from 'node:fs/promises';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/*
 * The design gallery, and the prototypes that live beside it.
 *
 * A prefix rather than a list of files: a list somebody has to remember to extend goes red on the
 * next prototype, and the easy answer (dressing the prototype to please the gate) ruins the
 * proposal. Everything under `routes/design/` exists to draw controls for comparison, so that is
 * what is named.
 */
const DRAWN_ON_PURPOSE = 'routes/design/';

/** The box `app.css` gives every field, as the property names that make it up. */
const FIELD_BOX = [
	'block-size',
	'height',
	'border',
	'border-radius',
	'background',
	'color',
	'font'
];

/** How many of those a rule has to restate before it is a copy rather than an adjustment. */
const A_COPY = 3;

/** A selector that is about a field rather than about something else in the same file. */
const ABOUT_A_FIELD = /(^|[\s,>+~])(input|textarea|\.input|\.area|\.field)\b/i;

function styleBlocks(source) {
	// Block comments only: a `//` inside a stylesheet is not a comment, and a markup comment cannot
	// be inside a `<style>`.
	const code = withoutComments(source, { markup: false, line: false });
	return [...code.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((match) => match[1]);
}

let count = 0;
const offenders = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where.startsWith(DRAWN_ON_PURPOSE)) continue;

	for (const block of styleBlocks(await readFile(path, 'utf8'))) {
		// The innermost `{ ... }` that holds no brace of its own, which is a declaration block.
		for (const match of block.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
			const [, selector, body] = match;
			// A state is an adjustment to a box, never the box itself.
			if (selector.includes(':hover') || selector.includes(':focus') || selector.includes('@')) {
				continue;
			}
			if (!ABOUT_A_FIELD.test(selector)) continue;
			const restated = FIELD_BOX.filter((name) =>
				new RegExp(String.raw`(?<![-\w])${name}\s*:`).test(body)
			);
			if (restated.length < A_COPY) continue;
			count += 1;
			offenders.push(`${where}  ${selector.trim().split('\n')[0].slice(0, 40)}`);
		}
	}
}

const complaint = await ratchet('one-field', 'fields', count, {
	what: 'field(s) dressed outside app.css',
	instead:
		'Instead: delete the box and keep only what is local to that screen: a width, a\n' +
		'    margin. `app.css` already gives every field its height, padding, edge, corner,\n' +
		'    ground and face, and says what those are.',
	offenders
});

if (complaint) {
	console.error(`\n  ${complaint}\n`);
	process.exit(1);
}

console.log(`One field: ${count} screen(s) still dress one themselves.`);
