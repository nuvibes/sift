// The rule `check_glyph_reach.js` holds every component to, as functions a test can drive.
//
// A glyph inside a button wears the button's own ink, so a destructive button turns red, glyph and
// word together, and nothing else tints it. A rule that colours `.icon` through a DESCENDANT
// combinator (`.list :global(.icon)`) colours every glyph under that element, including the glyph
// of a button somebody later puts in the row: a Remove whose cross comes out amber because the row
// tinted its folder mark. A button cannot defend its glyph against such a rule, because the two
// selectors weigh the same and the order stylesheets load in decides.
//
// So a rule that sets a glyph's colour names the glyph it means by a CHILD combinator
// (`.list > li > :global(.icon)`), which cannot reach inside a button, or it is on the list below.

/**
 * Rules that colour a glyph through a descendant combinator and are tolerated until they name
 * their glyph by a child combinator, by `file|selector`. An entry that no longer occurs fails the
 * gate until it is removed, so the list can only shrink. A new reach is never added here.
 *
 * @type {ReadonlyMap<string, string>}
 */
export const KNOWN_GLYPH_REACHES = new Map([
	['lib/components/common/KeptPill.svelte|.kept .name .icon', 'the pill holds no button'],
	['lib/components/common/Note.svelte|.caution .icon', "the note's own mark"],
	['lib/components/shell/PageHeader.svelte|h1 .icon', 'a heading holds no button'],
	['lib/settings-ui/Appearance.svelte|.what .icon', 'the preview holds no button'],
	['lib/settings-ui/SettingsTitle.svelte|.settings-title .icon', 'a heading holds no button']
]);

/** A declaration that sets the glyph's ink. `background-color` and `border-color` are not it. */
const INK = /(?:^|[;{\s])(?:color|fill)\s*:/;

/**
 * The selector with every `:global(...)` unwrapped to what is inside it, whitespace folded.
 *
 * @param {string} selector
 * @returns {string}
 */
export function unwrapGlobal(selector) {
	let out = '';
	for (let i = 0; i < selector.length;) {
		if (selector.startsWith(':global(', i)) {
			let depth = 0;
			let j = i + ':global'.length;
			for (; j < selector.length; j++) {
				if (selector[j] === '(') depth++;
				else if (selector[j] === ')' && --depth === 0) break;
			}
			out += selector.slice(i + ':global('.length, j);
			i = j + 1;
		} else {
			out += selector[i];
			i++;
		}
	}
	return out
		.replace(/\s+/g, ' ')
		.replace(/\s*([>+~])\s*/g, ' $1 ')
		.trim();
}

/**
 * The selectors of a list, split only at its top-level commas (not inside `:is()` or `:not()`).
 *
 * @param {string} list
 * @returns {string[]}
 */
export function splitSelectors(list) {
	/** @type {string[]} */
	const parts = [];
	let depth = 0;
	let start = 0;
	for (let i = 0; i < list.length; i++) {
		if (list[i] === '(') depth++;
		else if (list[i] === ')') depth--;
		else if (list[i] === ',' && depth === 0) {
			parts.push(list.slice(start, i));
			start = i + 1;
		}
	}
	parts.push(list.slice(start));
	return parts.map((one) => one.trim()).filter(Boolean);
}

/**
 * Whether a selector (already unwrapped) reaches a glyph through a descendant combinator: its last
 * compound names the class `icon`, and the combinator in front of that compound is a space.
 *
 * @param {string} selector
 * @returns {boolean}
 */
export function reachesGlyph(selector) {
	/** @type {string[]} */
	const tokens = [];
	let depth = 0;
	let word = '';
	for (const char of selector) {
		if (char === '(') depth++;
		if (char === ')') depth--;
		if (depth === 0 && char === ' ') {
			if (word) tokens.push(word);
			word = '';
		} else word += char;
	}
	if (word) tokens.push(word);
	if (tokens.length < 2) return false;
	const last = tokens[tokens.length - 1];
	const before = tokens[tokens.length - 2];
	const namesIcon = /\.icon(?![\w-])/.test(last.replace(/\([^)]*\)/g, ''));
	return namesIcon && !['>', '+', '~'].includes(before);
}

/**
 * @typedef {{ file: string, text: string }} Source
 * @typedef {{ file: string, selector: string }} Reach
 */

/**
 * Every rule in a set of components that sets a glyph's ink through a descendant combinator.
 * Comments must already be taken out of `text`.
 *
 * @param {Source[]} sources
 * @returns {Reach[]}
 */
export function glyphReaches(sources) {
	/** @type {Reach[]} */
	const found = [];
	for (const { file, text } of sources) {
		const style = text.match(/<style[^>]*>([\s\S]*?)<\/style>/);
		if (!style) continue;
		for (const rule of style[1].matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
			if (!INK.test(`;${rule[2]}`)) continue;
			for (const one of splitSelectors(rule[1])) {
				const selector = unwrapGlobal(one);
				if (reachesGlyph(selector)) found.push({ file, selector });
			}
		}
	}
	return found;
}

/**
 * The reaches not yet on the known list, and the known entries that no longer occur.
 *
 * @param {Reach[]} reaches
 * @param {ReadonlyMap<string, string>} [known]
 * @returns {{ fresh: Reach[], stale: string[] }}
 */
export function againstKnownGlyphs(reaches, known = KNOWN_GLYPH_REACHES) {
	const seen = new Set(reaches.map((one) => `${one.file}|${one.selector}`));
	return {
		fresh: reaches.filter((one) => !known.has(`${one.file}|${one.selector}`)),
		stale: [...known.keys()].filter((key) => !seen.has(key))
	};
}
