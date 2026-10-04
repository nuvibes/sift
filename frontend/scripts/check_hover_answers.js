// Every interactive surface answers the pointer, and the answer is not a shade of grey.
//
// ## The rule this enforces
//
// Every interactive surface changes under the cursor, and the change is animated over a duration
// token. Two failures are counted here:
//
//   - SNAP: a file with hover rules and no transition anywhere in it. The state change happens
//     between one frame and the next, which reads as the interface flickering rather than
//     responding.
//   - INK: a hover rule that changes `color` and nothing else. A colour change on text alone is
//     invisible to anyone not looking directly at it, and it fails at the low-contrast end.
//
// A shared component that snaps makes every screen snap, and no screenshot would ever show it,
// which is the argument for a machine rather than a one-off pass.
//
// ## Why a ratchet and not a ban
//
// The same reason `check_handrolled.js` gives. A gate that refused every one of these on the day it
// was written would fail on dozens of files, and the only ways to ship that are a rewrite of most
// of the interface in one change or a gate weakened until it passes. So it counts, the count may
// fall and may never rise, and every file fixed locks its own progress in permanently.
//
// ## The honest limits of the measure
//
// SNAP is per FILE, not per rule: it asks whether a file with hovers has any transition at all. A
// file could satisfy it with a transition on something unrelated. That is deliberate: deciding
// which declarations a given `transition` shorthand covers needs a real CSS parser and selector
// matching, and a gate that is nearly right about something that subtle is worse than one that is
// exactly right about something blunter. What it cannot be fooled about is the case that actually
// occurs: a file whose author did not think about motion at all.
//
// INK is per RULE and is exact: it reads the declarations inside one hover block.

import { readFile } from 'node:fs/promises';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/**
 * The pages that draw the interface ON PURPOSE, and so are not counted.
 *
 * The same two `check_handrolled.js` excuses, for the same reason: the gallery draws every
 * component deliberately and the bar prototype draws the screen bar's current shape beside two
 * proposals for it, hand-rolled glyphs and all.
 */
/*
 * The design gallery, and the prototypes that live beside it.
 *
 * A prefix rather than a list of files: a list somebody has to remember to extend goes red on the
 * next prototype, and the easy answer (dressing the prototype to please the gate) ruins the
 * proposal. Everything under `routes/design/` exists to draw controls for comparison, so that is
 * what is named.
 */
const DRAWN_ON_PURPOSE = 'routes/design/';

/**
 * The written judgement, in this exact spelling, followed by a reason.
 *
 * The same shape and the same reservations as the other ratchets': one line, in the file, exempting
 * the whole file, so that a file which opts out is a file somebody has to justify. It is counted
 * and printed rather than silently skipped, because an exemption nobody can see the size of is how
 * a ratchet stops being one.
 *
 * The case it is really for is a surface with no hover state ON PURPOSE: something drawn for
 * print, a specimen, a control whose only state is focus.
 */
const NO_HOVER_HERE = 'WHY NO HOVER:';

/** A style block, comments taken out so a `:hover` inside prose is not a rule. */
function styleBlocks(source) {
	// Block comments only: a `//` inside a stylesheet is not a comment, and a markup comment cannot
	// be inside a `<style>`.
	const code = withoutComments(source, { markup: false, line: false });
	return [...code.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((match) => match[1]);
}

/**
 * Every rule whose selector mentions `:hover`, as `{ selector, body }`.
 *
 * Nested `@media` and `:global()` are handled by the shape of the match rather than by parsing:
 * what is wanted is the innermost `{ ... }` that has no `{` in it, which is a declaration block.
 */
function hoverRules(block) {
	const found = [];
	for (const match of block.matchAll(/([^{}]*:hover[^{}]*)\{([^{}]*)\}/g)) {
		found.push({ selector: match[1].trim(), body: match[2] });
	}
	return found;
}

/**
 * Properties that CANNOT be animated, so a rule changing only these has nothing to transition.
 *
 * An underline appearing is a complete hover answer: it is what a bare word has instead of a
 * surface, and it is what the design itself reaches for where there is no ground to step. No engine
 * interpolates `text-decoration`, so counting a link that underlines itself as an un-animated state
 * change would be demanding something the site cannot do. Excluded from SNAP for that reason
 * and no other: a rule that changes an underline AND a colour is still counted, because the colour
 * half is animatable and ought to be animated.
 */
const INSTANT_BY_NATURE = new Set([
	'text-decoration',
	'text-decoration-line',
	'text-decoration-style',
	'text-underline-offset'
]);

/**
 * Whether the thing this hover rule is about wears an underline when nothing is pointing at it.
 *
 * The base selector is the hover selector with its pseudo-classes taken off. If a rule in the same
 * block whose selector holds that base declares `text-decoration: underline`, the word is marked
 * before anybody reaches it, which is what makes ink alone a legitimate answer there.
 */
function underlinedAtRest(block, selector) {
	const base = selector.split(',')[0].split(':')[0].trim();
	if (base.length === 0) return false;
	for (const match of block.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
		const [, where, body] = match;
		if (where.includes(':hover')) continue;
		if (!where.includes(base)) continue;
		if (/text-decoration:\s*underline/.test(body)) return true;
	}
	return false;
}

/** The property names declared in a rule body. */
function properties(body) {
	return body
		.split(';')
		.map((line) => line.split(':')[0].trim())
		.filter((name) => name.length > 0 && !name.startsWith('/'));
}

const counts = { snap: 0, ink: 0 };
const offenders = { snap: [], ink: [] };
const excused = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where.startsWith(DRAWN_ON_PURPOSE)) continue;

	const source = await readFile(path, 'utf8');
	if (source.includes(NO_HOVER_HERE)) {
		excused.push(where);
		continue;
	}

	for (const block of styleBlocks(source)) {
		const rules = hoverRules(block);
		if (rules.length === 0) continue;

		const animatable = rules.filter((rule) => {
			const names = properties(rule.body);
			return names.length > 0 && !names.every((name) => INSTANT_BY_NATURE.has(name));
		});
		if (animatable.length > 0 && !block.includes('transition') && !block.includes('animation')) {
			counts.snap += 1;
			offenders.snap.push(where);
		}

		for (const rule of rules) {
			// A rule keyed on an ANCESTOR's hover (`.person:hover .who`, `button:hover b`) is
			// not the hover answer, it is one part of an answer whose surface step is on the
			// ancestor. Counting it would report the same hover twice and blame the wrong rule for
			// it. A DESCENDANT is what follows a combinator: whitespace, `>`, `+` or `~`. A
			// pseudo-class that follows immediately (`:hover:not(:disabled)`) is the same
			// element still, and reading it as a descendant would hide real ink-only hovers from
			// the count.
			const descendant = rule.selector
				.split(',')
				.some((one) => /:hover[^,]*?[\s>+~]+[^\s>+~,][^,]*/.test(one));
			if (descendant) continue;
			// A `:global` hover reaches into a component whose own rules are in another file, so this
			// file is not the whole answer and cannot be judged on its own. The facet rows are the
			// standing case: the class goes to `Pressable`, which supplies the ground step, and the
			// rule here only deepens the ink on top of it. `check_handed_class.js` governs that seam.
			if (rule.selector.includes(':global(')) continue;
			// A word that is ALREADY underlined at rest is already marked as pressable before anybody
			// points at it, so ink alone is a confirmation of which one rather than the whole answer.
			if (underlinedAtRest(block, rule.selector)) continue;
			const names = properties(rule.body);
			if (names.length > 0 && names.every((name) => name === 'color')) {
				counts.ink += 1;
				offenders.ink.push(`${where}  ${rule.selector}`);
			}
		}
	}
}

const WHAT = {
	snap: {
		says: 'file(s) whose hover states are not animated',
		instead:
			'Instead: add `transition: <property> var(--dur-instant) var(--ease);` to the resting rule'
	},
	ink: {
		says: 'hover(s) that change the ink and nothing else',
		instead: 'Instead: step the surface too, or underline it where there is no surface to step'
	}
};

const complaints = [];
for (const id of Object.keys(counts)) {
	const complaint = await ratchet('hover', id, counts[id], {
		what: WHAT[id].says,
		instead: WHAT[id].instead,
		offenders: offenders[id],
		script: 'check_hover_answers.js'
	});
	if (complaint) complaints.push(complaint);
}

for (const complaint of complaints) console.error(`\n  ${complaint}\n`);

if (excused.length > 0) {
	console.log(`\n${excused.length} file(s) say WHY NO HOVER:`);
	for (const line of excused) console.log(`  ${line}`);
}

if (complaints.length > 0) process.exit(1);
console.log(`Hover answers: ${counts.snap} unanimated file(s), ${counts.ink} ink-only rule(s).`);
