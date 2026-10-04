// A `:global` rule has to start with a class this file alone writes.
//
// ## The failure this is for
//
// `:global(.scroll-root) { max-block-size: 60vh }` written in one panel for that panel's own
// scrolling box is not a rule about that panel. It is a rule about every scrolling box in the
// application, and it takes effect the moment that file's stylesheet is loaded, which is not only
// when the panel opens: SvelteKit preloads a route when the pointer touches a link to it,
// preloading loads its CSS, and client-side navigation never unloads a stylesheet. So pointing at a
// link can reshape every other screen for the rest of the session.
//
// ## Why the existing checks all miss it
//
//   - **dead-css** asks whether a selector matches anything. It matches a great deal.
//   - **styled** asks whether a class has a rule. It has one.
//   - **handed-class** asks whether a class handed to a component was dressed globally. This one is
//     dressed globally. That is the fault.
//
// None of them can see the difference between "global because the element is rendered elsewhere in
// MY markup" (which is correct and common: every bits-ui class in this app is that) and "global
// because I forgot that global means everywhere".
//
// ## The rule
//
// A `:global(...)` selector that starts a rule must be bounded by something this file owns. Two
// ways to satisfy that, and both are ordinary:
//
//   - a class in its FIRST compound is one this file writes and no other file does: on its own
//     element, on a component it renders, or handed to a library through a `class` prop.
//     `:global(.btn.folders)` is every button carrying this file's `folders`, which is this file's
//     button. Writing the class is not enough on its own: a class name is the same name in every
//     file, so a calendar's `:global(.day)` would dress a log's `<div class="day">` as well.
//   - the selector CONTINUES outside the global part, so the tail is scoped to this file:
//     `:global(.frame-body) .row` is every row this component draws, in whatever frame it sits in.
//
// `.mine :global(.theirs)` needs nothing: it does not start with `:global`, so it is already
// bounded.
//
// A selector with no class at all (`:global(main:has(.settings .pane))`) is left alone. Those
// are deliberate reaches at the layout, they are rare, and each one carries a comment saying why.
// A class several components write so that one rule dresses them all is named in
// `SHARED_BY_DESIGN`; the reaches this rule found already standing are in `KNOWN_REACHES`, which
// only shrinks. Both live in `lib/anchored-globals.js`, with the rule.
//
// Run: node scripts/check_anchored_globals.js

import { readFile } from 'node:fs/promises';

import { againstKnown, looseGlobals } from './lib/anchored-globals.js';
import { everySvelteFile, fromSource, SOURCE } from './lib/tree.js';

const files = await everySvelteFile(SOURCE);
const sources = await Promise.all(
	files.map(async (file) => ({ file: fromSource(file), text: await readFile(file, 'utf8') }))
);
const { fresh: loose, stale } = againstKnown(looseGlobals(sources));

if (loose.length > 0) {
	console.error(
		"These rules are global and start with a class that is not this file's alone, so they reach\n" +
			'every screen in the application the moment this stylesheet loads, which happens when\n' +
			'somebody points at a link to it, not when they open it:\n'
	);
	for (const one of loose) {
		const whose =
			one.others.length > 0
				? `.${one.first} is also written by ${one.others.join(', ')}.`
				: `.${one.first} belongs to somebody else.`;
		console.error(`  ${one.file}\n    ${one.selector}\n    ${whose}`);
	}
	console.error(
		'\nAnchor it: start the rule at a class only this file writes, or wrap what it dresses in an\n' +
			"element of this file's own and write `.mine :global(.theirs)`."
	);
	process.exit(1);
}

if (stale.length > 0) {
	console.error(
		'These are listed as reaching other files and no longer do. Take them off the list:\n'
	);
	for (const one of stale) console.error(`  ${one}`);
	process.exit(1);
}

console.log(`anchored-globals: ${files.length} components checked; every global rule is bounded.`);
