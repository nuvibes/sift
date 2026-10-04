// A component that puts a ceiling on a box holding a `Scroller` has to give that box a HEIGHT.
//
// ## The fault
//
// `max-block-size` with no `overflow` beside it does not scroll: it spills. Put a `Scroller`
// inside such a box and it is worse than a plain clip, because the scroller looks like it is doing
// its job: it renders, it has a viewport, and that viewport lays itself out at the full height of
// its content and paints straight out through the ceiling, over the buttons below it. Nothing
// errors.
//
// ## Why `check_one_scrollbar.js` cannot see it
//
// That gate asks whether a screen declares its own scrolling. This shape declares none: the
// scrolling is the shared region's, correctly, and the fault is the missing height on the box
// around it. The two gates are about opposite halves of the same mistake.
//
// ## What counts as giving it a height
//
// One of three, and each is a real fix rather than a spelling:
//
//   1. The cap is on the scroll root itself, anchored to one of this file's own classes:
//      `.capped :global(.scroll-root) { max-block-size: ... }`. The cap is then ON the box that
//      scrolls, which is the shape `MoveFacesDialog` and `CellFilters` use.
//   2. The capped box is a grid with an explicit `grid-template-rows`, so the cap becomes a track
//      the child is bounded BY rather than a limit it ignores. `PickDialog` and `EntityHeader`.
//   3. The capped box declares its own `overflow`, in which case it is not this shape at all: it
//      is a deliberate clip and `check_one_scrollbar.js` is the gate that has an opinion about it.
//
// A bare `:global(.scroll-root)` rule with no anchor is not accepted: it is a rule about every
// scrolling box in the application. `check_anchored_globals.js` catches that in general.
//
// ## Why a ratchet
//
// The same reason every other design gate here is one, and it is at zero. It may fall and may never
// rise.

import { readFile } from 'node:fs/promises';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/** The component that IS the scrolling region. Its own rules are the thing, not a use of it. */
const THE_SCROLLER = 'lib/components/common/Scroller.svelte';

/**
 * Every `max-block-size` / `max-height` declaration in a rule body, with its value captured.
 *
 * Either axis-relative spelling; the logical one is what this codebase uses.
 */
const CEILING_DECLARATION = /(?:max-block-size|max-height)\s*:\s*([^;}]*)/gi;

/**
 * The values that are NOT a ceiling: `none` and the four keywords that all resolve to it, which
 * is this property's initial value.
 */
const NO_CEILING = /^(?:none|initial|unset|revert|revert-layer)$/i;

/**
 * Does this rule body put a real ceiling on the box?
 *
 * The value is read, not just the property name: `max-block-size: none` is the opposite of a
 * ceiling. It is how a rule cancels one it would otherwise inherit.
 *
 * Written as a loop over captured values rather than as one regex with a negative lookahead,
 * because the lookahead version passes its own mutation: `\s*` before the lookahead backtracks to
 * zero, the lookahead is then tried against the space in front of `none`, it fails, and the
 * negative lookahead therefore succeeds. `check_no_native_chrome.js` records the same trap.
 */
function hasACeiling(body) {
	CEILING_DECLARATION.lastIndex = 0;
	for (const [, value] of body.matchAll(CEILING_DECLARATION)) {
		if (!NO_CEILING.test(value.trim())) return true;
	}
	return false;
}

/** A cap aimed at the scroll root through one of this file's own classes. Fix 1. */
const ANCHORED_AT_THE_ROOT =
	/\.[\w-]+[^{}]*:global\(\s*\.scroll-root\s*\)[^{}]*\{[^{}]*(?:max-block-size|max-height)\s*:/;

/** The `<style>` block, with comments stripped so prose about the trap is never a match. */
function styleOf(source) {
	const block = source.match(/<style[^>]*>([\s\S]*?)<\/style>/);
	// Block comments only: a `//` in a stylesheet is not a comment, and there is no markup here.
	return block ? withoutComments(block[1], { markup: false, line: false }) : '';
}

/** Every `selector { ... }` rule in a stylesheet, as [selector, body] pairs. Nesting-tolerant. */
function rules(style) {
	const found = [];
	const pattern = /([^{}]+)\{([^{}]*)\}/g;
	let match;
	while ((match = pattern.exec(style)) !== null) found.push([match[1].trim(), match[2]]);
	return found;
}

const offenders = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where === THE_SCROLLER) continue;
	const source = await readFile(path, 'utf8');
	// Only files that actually put a Scroller on the page. A cap on a picture or a paragraph is
	// somebody's layout, not this fault.
	if (!/<Scroller\b/.test(source)) continue;

	const style = styleOf(source);
	if (!style) continue;
	if (ANCHORED_AT_THE_ROOT.test(style)) continue; // fix 1

	for (const [selector, body] of rules(style)) {
		if (!hasACeiling(body)) continue;
		// fix 3: a box that says what it does with its overflow is not this shape.
		if (/overflow(?:-block|-inline|-x|-y)?\s*:/.test(body)) continue;
		// fix 2: a grid whose rows are declared turns the cap into a track.
		if (/grid-template-rows\s*:/.test(body)) continue;
		// A cap on a replaced element is about the media, not about a scrolling child.
		if (/^(?:video|img|canvas|iframe)\b/.test(selector)) continue;
		offenders.push(`${where}  ${selector.replace(/\s+/g, ' ')}`);
	}
}

/* A fall is recorded by the shared ratchet's `--record`, which the pre-commit hook runs: one
   spelling for every gate. */
const complaint = await ratchet('capped-scroller', 'cappedWithoutAHeight', offenders.length, {
	what: 'capped boxes around a Scroller with no height',
	instead:
		'A `max-block-size` with no `overflow` beside it does not scroll, it SPILLS, and a ' +
		'Scroller inside one lays out at its full content height and paints out of the box.\n' +
		'    Fix it one of three ways: put the cap on `.something :global(.scroll-root)`, give the ' +
		'capped box `grid-template-rows: minmax(0, 1fr)`, or declare its `overflow` and mean it.',
	offenders
});

if (complaint) {
	console.error(complaint);
	process.exit(1);
}

console.log(`capped-scroller: ${offenders.length} (at the recorded number)`);
