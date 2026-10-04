// What `check_button_glyphs.js` holds every button to, as functions a test can drive.
//
// Every button is one of three shapes, sorted by what pressing it does (the rule is written out in
// full at the head of `lib/components/common/Button.svelte`):
//
//   - glyph and words: an act on a thing, wearing the act's glyph (Add wears `add`, Save `save`);
//   - words only: a navigation, and an answer to what the screen has asked (Cancel, Done, Back);
//   - glyph only: where the row has no room for words, and then always inside a `Tooltip`.
//
// Read from markup with its comments taken out. A `Button` or `SplitButton` is read for its glyph
// (`icon=`, the `{icon}` shorthand, or an `<Icon` among its children), its words (whatever its
// children draw that is not a glyph, a spinner or a block tag) and whether a `<Tooltip` is open
// around it. Only words written as plain text are judged by what they say: `{label}` could say
// anything, and a guess would either pass everything or accuse the wrong line.
//
// A declared verb (`common/verbs.ts`) is not read here: `Verb.icon` is a required `IconName`, so a
// verb with no glyph is already a type error, and a second copy of that rule would only drift.

import { ACTS } from '../../src/lib/design/button-glyphs.ts';

/** The button-shaped components this reads. `Pressable` is a surface, not a button: its children
 *  are a picture or a card, not words or a glyph. */
const BUTTONS = /<(Button|SplitButton)\b/g;

/**
 * Which way a control moves, not what it does: punctuation rather than a glyph. A navigation may
 * wear one on the side it points to (Back, Previous, a disclosure's chevron), and an answer may too.
 */
export const DIRECTION_MARKS = new Set([
	'arrow_back',
	'arrow_forward',
	'arrow_upward',
	'arrow_downward',
	'chevron_left',
	'chevron_right',
	'expand_more',
	'expand_less',
	'keyboard_arrow_right',
	'first_page',
	'last_page'
]);

/**
 * The acts and the glyphs each may wear, from the one table the app's rows read too
 * (`src/lib/design/button-glyphs.ts`), so a row drawing its button from a verb and this gate agree.
 */
export { ACTS };

/** The answers, by first word: what the screen asked is already on it, so the words are enough. */
export const ANSWERS = new Set([
	'Back',
	'Cancel',
	'Close',
	'Dismiss',
	'Done',
	'Next',
	'Previous',
	'Skip'
]);

/** A navigation, by first word, where the button does not say so by calling `goto`. */
const GOES = new Set(['Browse', 'Open', 'See', 'Show', 'View']);

/**
 * Icon-only buttons that are left without a tooltip, by `file|aria-label` as written, each with
 * the reason. An entry that no longer occurs fails the gate, so the list can only shrink.
 *
 * @type {ReadonlyMap<string, string>}
 */
export const UNLABELLED = new Map([
	[
		'lib/components/player/PlayerBar.svelte|"More controls"',
		'opens its tray under the pointer, so a label would sit on top of what it opened'
	],
	[
		'lib/components/player/StageNotice.svelte|{label}',
		'the mark opens its own words on hover and on focus; a tooltip would say them twice'
	]
]);

/**
 * The index just past the `>` that closes the tag opening at `open`, reading `{...}` expressions
 * whole so an arrow function's `=>` or a comparison cannot end the tag early.
 *
 * @param {string} code
 * @param {number} open index of the `<`
 * @returns {{ end: number, selfClosing: boolean } | null}
 */
export function tagEnd(code, open) {
	let depth = 0;
	let quote = '';
	for (let at = open + 1; at < code.length; at++) {
		const char = code[at];
		if (quote) {
			if (char === '\\') at++;
			else if (char === quote) quote = '';
			continue;
		}
		if (depth > 0 && (char === "'" || char === '`' || char === '"')) quote = char;
		else if (depth === 0 && char === '"') quote = char;
		else if (char === '{') depth++;
		else if (char === '}') depth--;
		else if (char === '>' && depth === 0) {
			return { end: at + 1, selfClosing: code[at - 1] === '/' };
		}
	}
	return null;
}

/**
 * What a button's children say, with every tag and every block tag taken out.
 *
 * `{#if busy}<Spinner />{:else}<Icon name="x" />{/if}` is a glyph and no words; `{label}`,
 * `{@render children()}` and plain text are words.
 *
 * @param {string} children
 */
function wordsIn(children) {
	let text = '';
	let at = 0;
	for (const match of children.matchAll(/<\/?[A-Za-z]/g)) {
		if (match.index < at) continue;
		text += children.slice(at, match.index);
		const tag = tagEnd(children, match.index);
		at = tag === null ? children.length : tag.end;
	}
	text += children.slice(at);
	return text
		.replace(/\{[#:/][^}]*\}/g, '')
		.replace(/\{@const[^}]*\}/g, '')
		.replace(/\s+/g, ' ')
		.trim();
}

/**
 * Whether the markup before a button leaves a `<Tooltip` open around it.
 *
 * @param {string} before
 */
function insideTooltip(before) {
	let open = 0;
	for (const match of before.matchAll(/<Tooltip\b|<\/Tooltip>/g)) {
		if (match[0] === '</Tooltip>') open = Math.max(0, open - 1);
		else {
			const end = tagEnd(before, match.index);
			if (end && !end.selfClosing) open++;
		}
	}
	return open > 0;
}

/**
 * An attribute's value as written, quotes or braces included, or null.
 *
 * @param {string} tag
 * @param {string} name
 */
function attribute(tag, name) {
	const found = new RegExp(String.raw`\s${name}=`).exec(tag);
	if (!found) return null;
	const start = found.index + found[0].length;
	if (tag[start] === '"') return tag.slice(start, tag.indexOf('"', start + 1) + 1);
	if (tag[start] !== '{') return null;
	let depth = 0;
	for (let at = start; at < tag.length; at++) {
		if (tag[at] === '{') depth++;
		else if (tag[at] === '}' && --depth === 0) return tag.slice(start, at + 1);
	}
	return null;
}

/**
 * @typedef {object} ButtonRead
 * @property {string} component `Button` or `SplitButton`
 * @property {number} line counted from 1
 * @property {string | null} icon the glyph as written: `"add"`, `{name}`, or `<Icon>` for a child
 * @property {string} text the words, as written, whitespace folded
 * @property {string | null} tone the tone as written
 * @property {string | null} named the `aria-label` as written
 * @property {boolean} tooltip a `<Tooltip` is open around it
 * @property {boolean} navigates it calls `goto`, carries an `href`, or its words say it goes
 */

/**
 * Every button in one file's markup, read.
 *
 * @param {string} code a component with its comments taken out
 * @returns {ButtonRead[]}
 */
export function buttonsIn(code) {
	/** @type {ButtonRead[]} */
	const found = [];
	for (const match of code.matchAll(BUTTONS)) {
		const tag = tagEnd(code, match.index);
		if (tag === null) continue;
		const opening = code.slice(match.index, tag.end);
		let children = '';
		if (!tag.selfClosing) {
			const close = code.indexOf(`</${match[1]}`, tag.end);
			children = close === -1 ? '' : code.slice(tag.end, close);
		}
		const text = wordsIn(children);
		const icon =
			attribute(opening, 'icon') ??
			(/\s\{icon\}/.test(opening) ? '{icon}' : /<Icon\b/.test(children) ? '<Icon>' : null);
		found.push({
			component: match[1],
			line: code.slice(0, match.index).split('\n').length,
			icon,
			text,
			tone: attribute(opening, 'tone'),
			named: attribute(opening, 'aria-label'),
			tooltip: insideTooltip(code.slice(0, match.index)),
			navigates: /\bgoto\(|\shref=/.test(opening) || GOES.has(firstWord(text) ?? '') || false
		});
	}
	return found;
}

/**
 * The first word of words written as plain text, or null when they open on an expression.
 *
 * @param {string} text
 */
function firstWord(text) {
	return /^[A-Z][a-z]+/.exec(text)?.[0] ?? null;
}

/** A glyph written as a plain string, without its quotes, or null when it is worked out. */
const literal = (/** @type {string | null} */ icon) =>
	icon !== null && icon.startsWith('"') ? icon.slice(1, -1) : null;

/**
 * What the gate refuses in one file, and the navigations that wear a glyph (counted, not refused).
 *
 * @param {string} code a component with its comments taken out
 * @param {string} [file] the file, as the gates print it, for the `UNLABELLED` list
 * @param {Set<string>} [excused] collects the `UNLABELLED` entries that were met
 * @returns {{ faults: { line: number, what: string }[], glyphedNavigations: { line: number, what: string }[] }}
 */
export function buttonFaultsIn(code, file = '', excused = new Set()) {
	/** @type {{ line: number, what: string }[]} */
	const faults = [];
	/** @type {{ line: number, what: string }[]} */
	const glyphedNavigations = [];
	for (const one of buttonsIn(code)) {
		const glyph = literal(one.icon);
		const word = firstWord(one.text);
		const says = one.text.length > 40 ? `${one.text.slice(0, 40)}...` : one.text;
		const wordsOnly = one.tone === '"quiet"' || one.tone === '"link"';

		if (one.icon !== null && one.text === '' && !one.tooltip) {
			const key = `${file}|${one.named ?? ''}`;
			if (UNLABELLED.has(key)) excused.add(key);
			else
				faults.push({
					line: one.line,
					what: `an icon-only ${one.component} (${one.named ?? 'no name'}) with no Tooltip around it`
				});
		}

		if (word !== null && ACTS.has(word) && !wordsOnly) {
			const allowed = /** @type {readonly string[]} */ (ACTS.get(word) ?? []);
			if (one.icon === null)
				faults.push({
					line: one.line,
					what: `"${says}" is an act and wears no glyph (${allowed[0]})`
				});
			else if (glyph !== null && !allowed.includes(glyph))
				faults.push({
					line: one.line,
					what: `"${says}" wears ${glyph}, and ${word} wears ${allowed.join(' or ')}`
				});
		}

		if (one.icon !== null && one.text !== '' && (glyph === null || !DIRECTION_MARKS.has(glyph))) {
			if (wordsOnly && word !== null)
				faults.push({ line: one.line, what: `"${says}" is a word that acts and wears a glyph` });
			else if (word !== null && ANSWERS.has(word))
				faults.push({ line: one.line, what: `"${says}" is an answer and wears a glyph` });
			else if (one.navigates)
				glyphedNavigations.push({
					line: one.line,
					what: `"${says}" goes somewhere and wears a glyph`
				});
		}
	}
	return { faults, glyphedNavigations };
}
