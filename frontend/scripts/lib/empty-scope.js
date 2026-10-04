// What `check_empty_scope.js` refuses, as a function of one file's code (comments already taken
// out), so the unit suite can hand it lines and watch it refuse them.
//
// Every `<Empty` says what the empty thing is: `scope="page"` where it IS the screen, and
// `scope="block"` where it is a list or a band inside one. Refused: an `Empty` naming no scope,
// and the older `quiet` flag, which said the same thing as `scope="block"` in a second spelling.

/** @param {string} text @param {number} index */
function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

/**
 * The opening tag that starts at `start`, up to its closing `>`, braces and quotes skipped, so an
 * attribute holding an arrow function does not end the tag early.
 *
 * @param {string} code
 * @param {number} start
 */
function tagAt(code, start) {
	let depth = 0;
	let quote = '';
	for (let at = start; at < code.length; at++) {
		const char = code[at];
		if (quote) {
			if (char === quote) quote = '';
			continue;
		}
		if (depth === 0 && (char === '"' || char === "'")) quote = char;
		else if (char === '{') depth++;
		else if (char === '}') depth--;
		else if (char === '>' && depth === 0) return code.slice(start, at + 1);
	}
	return code.slice(start);
}

/**
 * Every `Empty` in `code` that says no scope or says it the old way, as `{ line, what }`.
 *
 * @param {string} code
 * @returns {{ line: number, what: string }[]}
 */
export function unscopedEmptiesIn(code) {
	/** @type {{ line: number, what: string }[]} */
	const found = [];
	for (const match of code.matchAll(/<Empty\b/g)) {
		const tag = tagAt(code, match.index);
		/* The attributes with every `{...}` value blanked, so a word inside an expression (a
		   variable called `quiet`, a sentence) is not read as an attribute. */
		const bare = tag.replace(/\{[^{}]*\}/g, '{}');
		const line = lineOf(code, match.index);
		if (/\squiet(?=[\s=/>])/.test(bare)) {
			found.push({ line, what: 'the quiet flag (write scope="block")' });
		} else if (!/\sscope=/.test(bare)) {
			found.push({ line, what: 'no scope (scope="page" or scope="block")' });
		}
	}
	return found;
}
