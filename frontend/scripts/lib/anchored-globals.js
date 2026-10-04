// The rule `check_anchored_globals.js` holds every component to, as functions a test can drive.
//
// A rule that starts with `:global(...)` and has nothing scoped after it is a rule about the whole
// application, whatever file it sits in. It is bounded only when its first compound names a class
// that one file alone writes: then every element it can reach is that file's. Two ways it is not:
//
//   - **foreign**: no class in the first compound is one this file writes at all.
//   - **shared**: this file writes the class, and so does another file for an element of its own,
//     so the rule dresses theirs too: a calendar's `.day` would dress a log's day heading.
//
// The fix for either is the same: start the rule at a class only this file writes
// (`:global(.date-content .day)`), or at an element of this file's own (`.mine :global(.theirs)`).

/**
 * Classes several components write ON PURPOSE, so that one rule dresses all of them: the design's
 * shared surfaces. A name here is a decision that every element carrying it is the same thing.
 *
 * @type {ReadonlyMap<string, string>}
 */
export const SHARED_BY_DESIGN = new Map([
	[
		'ui-menu',
		'the one menu surface every menu, select and suggestion list opens, and the `.item` rows a menu draws on it'
	],
	[
		'section-stack',
		'a page of settings groups: SectionHeading draws the hairline between the groups on it'
	]
]);

/**
 * Rules that reach other files' elements and are tolerated until they are anchored, by
 * `file|class`. An entry that no longer occurs fails the gate until it is removed, so the list can
 * only shrink. It is empty: a new reach is anchored, never added here.
 *
 * @type {ReadonlyMap<string, string>}
 */
export const KNOWN_REACHES = new Map();

/**
 * Every class name a component writes anywhere in its markup.
 *
 * Deliberately generous. A false ACCUSATION here is a gate somebody has to argue with, so anything
 * that looks like a class name in an attribute, a `class:` directive, or a `class`-ish prop handed
 * to a component counts as written. What it will not find is a class this file never mentions.
 *
 * @param {string} markup
 * @returns {Set<string>}
 */
export function classesWritten(markup) {
	/** @type {Set<string>} */
	const owned = new Set();
	/** @param {string} text */
	const add = (text) => {
		for (const word of text.match(/[A-Za-z][\w-]*/g) ?? []) owned.add(word);
	};
	// Both quote styles, because a gate that depends on one of them reads a file differently from
	// the compiler.
	for (const found of markup.matchAll(/[\w]*[Cc]lass(?:Name)?\s*=\s*"([^"]*)"/g)) add(found[1]);
	for (const found of markup.matchAll(/[\w]*[Cc]lass(?:Name)?\s*=\s*'([^']*)'/g)) add(found[1]);
	for (const found of markup.matchAll(/[\w]*[Cc]lass(?:Name)?\s*=\s*\{([^}]*)\}/g)) add(found[1]);
	for (const found of markup.matchAll(/class:([\w-]+)/g)) owned.add(found[1]);
	return owned;
}

/**
 * Every class in the FIRST compound of a selector: the part before any combinator.
 *
 * The compound rather than the single first name, because a rule can be bounded by the second
 * half of one: `:global(.btn.folders)` is every button that also carries this file's `folders`.
 *
 * @param {string} selector
 * @returns {string[]}
 */
export function firstCompound(selector) {
	const head = selector.trim().split(/[\s>+~]+/)[0] ?? '';
	return (head.match(/\.([\w-]+)/g) ?? []).map((one) => one.slice(1));
}

/**
 * @typedef {{ file: string, text: string }} Source
 * @typedef {{ file: string, selector: string, first: string, others: string[] }} Loose
 */

/**
 * Every unbounded global rule in a set of components.
 *
 * `others` is empty for a foreign class and names the other files that write a shared one.
 *
 * @param {Source[]} sources
 * @returns {Loose[]}
 */
export function looseGlobals(sources) {
	/** @type {Map<string, Set<string>>} */
	const writers = new Map();
	/** @type {Map<string, Set<string>>} */
	const writes = new Map();
	for (const { file, text } of sources) {
		const at = text.indexOf('<style');
		const owned = classesWritten(at === -1 ? text : text.slice(0, at));
		writes.set(file, owned);
		for (const name of owned) {
			if (!writers.has(name)) writers.set(name, new Set());
			writers.get(name)?.add(file);
		}
	}

	/** @type {Loose[]} */
	const loose = [];
	for (const { file, text } of sources) {
		const at = text.indexOf('<style');
		if (at === -1) continue;
		const owned = writes.get(file) ?? new Set();
		for (const line of text.slice(at).split('\n')) {
			const trimmed = line.trim();
			// Only a selector that BEGINS with `:global(` is unanchored: `.capped :global(.x)` is
			// bounded by `.capped`, which is this file's.
			if (!trimmed.startsWith(':global(')) continue;
			// The global's OWN closing bracket, not the first one: `:global(x:not(:disabled))` closes
			// twice, and reading the first `)` would leave a tail of `)` that counts as a continuation.
			let closes = -1;
			for (let i = ':global'.length, depth = 0; i < trimmed.length; i++) {
				if (trimmed[i] === '(') depth++;
				else if (trimmed[i] === ')' && --depth === 0) {
					closes = i;
					break;
				}
			}
			if (closes === -1) continue;
			const inside = trimmed.slice(':global('.length, closes);
			// A selector that CONTINUES after the global part is bounded by that continuation: the
			// tail is scoped to this file, so the rule can only land on this file's elements.
			const tail = trimmed
				.slice(closes + 1)
				.replace(/\s*[{,]\s*$/, '')
				.trim();
			if (tail.length > 0) continue;

			// A selector with no class at all (`:global(main:has(.settings .pane))`) is a deliberate
			// reach at the layout, rare, and each one carries a comment saying why.
			const head = firstCompound(inside);
			if (head.length === 0) continue;
			const selector = trimmed.replace(/\s*[{,]\s*$/, '');
			// A class the design shares on purpose is one ANY file may start a rule at, including a
			// file that never writes it: SectionHeading dresses the page others lay out.
			if (head.some((one) => SHARED_BY_DESIGN.has(one))) continue;
			const mine = head.filter((one) => owned.has(one));
			if (mine.length === 0) {
				loose.push({ file, selector, first: head[0], others: [] });
				continue;
			}
			const alone = mine.find((one) => (writers.get(one)?.size ?? 0) <= 1);
			if (alone !== undefined) continue;
			const others = [...(writers.get(mine[0]) ?? [])].filter((one) => one !== file).sort();
			loose.push({ file, selector, first: mine[0], others });
		}
	}
	return loose;
}

/**
 * The loose rules not yet on the known list, and the known entries that no longer occur.
 *
 * @param {Loose[]} loose
 * @param {ReadonlyMap<string, string>} [known]
 * @returns {{ fresh: Loose[], stale: string[] }}
 */
export function againstKnown(loose, known = KNOWN_REACHES) {
	const seen = new Set(loose.map((one) => `${one.file}|${one.first}`));
	return {
		fresh: loose.filter((one) => !known.has(`${one.file}|${one.first}`)),
		stale: [...known.keys()].filter((key) => !seen.has(key))
	};
}
