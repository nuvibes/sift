// What a toast says, read out of the source: the client's calls into the toast doors and the
// server's toast sentences, and the rules both are held to. Driven by `check_toast_stops.js` and
// by `src/lib/build/toast-words.test.ts`.
//
// A message is read as the person sees it: a literal whole, each branch of a ternary, the
// fallback after `??` or `||`, and an array of pieces joined, a piece that is not a literal
// standing in as one word. What cannot be read that way (a variable, a call) is judged where it
// is written, by its own door or not at all.

/** The calls whose argument is a toast's words, and which argument. */
export const DOORS = [
	{ pattern: /\btoasts\.show\s*\(/g, at: 0 },
	{ pattern: /\btoasts\.settle\s*\(/g, at: 1 },
	{ pattern: /\btoasts\.advance\s*\(/g, at: 2 },
	{ pattern: /(?<![.\w])decided\s*\(/g, at: 0 }
];

/** A stop, a question mark or an exclamation with white space after it: a second sentence. */
const BREAK = /[.?!]\s/;

/** The words a thing is said by, as the last part of what an interpolation reads. */
const NAMED =
	/(?:^|\.)(?:name|username|person_name|site_name|box_name|filename|original_filename)$/;
const NAMING_CALL = /^(?:nameOf|named|shownAs)\(/;

/** Where a clause is spliced on with a comma: a comma and then a new subject. */
const SPLICE =
	/, (?:it|it's|its|they|they're|this|that's|there's|you|you're|we|Sift|X (?:couldn't|could|was|were|is|are|has|have|had)) /;

/**
 * The end of a quoted run starting at `at` (the quote), in JavaScript or Python text. A template's
 * `${...}` is skipped whole, so a quote inside one does not end it.
 */
/** @param {string} text @param {number} at @returns {number} */
function quoteEnd(text, at) {
	const quote = text[at];
	let i = at + 1;
	while (i < text.length) {
		const c = text[i];
		if (c === '\\') i += 2;
		else if (c === quote) return i;
		else if (quote === '`' && c === '$' && text[i + 1] === '{') i = closing(text, i + 1) + 1;
		else i += 1;
	}
	return text.length;
}

/** The index of the bracket closing the one at `at`. */
/** @param {string} text @param {number} at @returns {number} */
export function closing(text, at) {
	let depth = 0;
	for (let i = at; i < text.length; i += 1) {
		const c = text[i];
		if (c === '"' || c === "'" || c === '`') i = quoteEnd(text, i);
		else if (c === '/' && text[i + 1] === '/')
			i = text.indexOf('\n', i) === -1 ? text.length : text.indexOf('\n', i);
		else if (c === '/' && text[i + 1] === '*') i = text.indexOf('*/', i + 2) + 1;
		else if ('([{'.includes(c)) depth += 1;
		else if (')]}'.includes(c)) {
			depth -= 1;
			if (depth === 0) return i;
		}
	}
	return text.length;
}

/** `text` split on `sep` where it stands at the top level: not inside a bracket or a quote. */
/** @param {string} text @param {string} sep @returns {string[]} */
function topLevel(text, sep) {
	/** @type {string[]} */
	const parts = [];
	let from = 0;
	for (let i = 0; i < text.length; i += 1) {
		const c = text[i];
		if (c === '"' || c === "'" || c === '`') i = quoteEnd(text, i);
		else if ('([{'.includes(c)) i = closing(text, i);
		else if (text.startsWith(sep, i)) {
			parts.push(text.slice(from, i));
			from = i + sep.length;
			i += sep.length - 1;
		}
	}
	parts.push(text.slice(from));
	return parts.map((one) => one.trim());
}

/** A ternary's two outcomes, or null: the top-level `?` (not `?.` or `??`) and its own `:`. */
/** @param {string} text @returns {string[] | null} */
function ternary(text) {
	let asked = -1;
	let depth = 0;
	for (let i = 0; i < text.length; i += 1) {
		const c = text[i];
		if (c === '"' || c === "'" || c === '`') i = quoteEnd(text, i);
		else if ('([{'.includes(c)) i = closing(text, i);
		else if (c === '?' && text[i + 1] === '?') i += 1;
		else if (c === '?' && text[i + 1] !== '.') {
			if (asked === -1) asked = i;
			else depth += 1;
		} else if (c === ':' && asked !== -1) {
			if (depth === 0) return [text.slice(asked + 1, i), text.slice(i + 1)];
			depth -= 1;
		}
	}
	return null;
}

/** The arguments of the call whose `(` is at `open`. */
/** @param {string} text @param {number} open @returns {string[]} */
export function argumentsAt(text, open) {
	return topLevel(text.slice(open + 1, closing(text, open)), ',').filter((one) => one !== '');
}

/** The literal an expression IS, without its quotes, or null. */
/** @param {string} expression @returns {string | null} */
function literal(expression) {
	const e = expression.trim();
	if (!/^['"`]/.test(e) || quoteEnd(e, 0) !== e.length - 1) return null;
	return e.slice(1, -1);
}

/**
 * Every message one argument can say, as literals with their interpolations kept, and an array's
 * non-literal pieces as `${}`. Empty where nothing can be read.
 */
/** @param {string} expression @returns {string[]} */
export function messagesOf(expression) {
	const e = expression.trim();
	const whole = literal(e);
	if (whole !== null) return [whole];
	const split = ternary(e);
	if (split) return split.flatMap(messagesOf);
	for (const fallback of ['??', '||']) {
		const sides = topLevel(e, fallback);
		if (sides.length > 1) return messagesOf(sides[sides.length - 1]);
	}
	if (e.startsWith('[') && closing(e, 0) === e.length - 1) {
		const pieces = topLevel(e.slice(1, -1), ',').filter((one) => one !== '');
		return [pieces.map((one) => literal(one) ?? '${}').join('')];
	}
	return [];
}

/** A message as punctuation sees it: every interpolation one word. */
/** @param {string} message */
function plain(message) {
	return message.replace(/\$\{(?:[^{}]|\{[^{}]*\})*\}/g, 'X');
}

/** Whether a toast of one sentence ends in a stop the store would take off. */
/** @param {string} message */
export function wouldBeCorrected(message) {
	const words = plain(message);
	if (!words.endsWith('.') || words.endsWith('..')) return false;
	return !BREAK.test(words);
}

/** What is wrong with one message's punctuation, in words, or an empty list. */
/** @param {string} message @param {{ stops?: boolean }} [options] @returns {string[]} */
export function punctuation(message, { stops = true } = {}) {
	const words = plain(message);
	/** @type {string[]} */
	const wrong = [];
	if (stops && wouldBeCorrected(message)) wrong.push('a full stop on one sentence');
	if (words.includes(';')) wrong.push('a semicolon');
	if (SPLICE.test(words)) wrong.push('a comma splicing two sentences');
	if (/\s[,.;:!?](?:\s|$)/.test(words) || / {2}/.test(words)) wrong.push('a stray space');
	if (/ -{2} /.test(words)) wrong.push('a double hyphen');
	return wrong;
}

/** The interpolations in a message that say a thing's name as words. */
/** @param {string} message @returns {string[]} */
export function namesAsWords(message) {
	/** @type {string[]} */
	const found = [];
	for (const [, inside] of message.matchAll(/\$\{((?:[^{}]|\{[^{}]*\})*)\}/g)) {
		const read = inside
			.split(/\?\?|\|\|/)[0]
			.replaceAll('?.', '.')
			.replace(/\s+/g, '');
		if (NAMED.test(read) || NAMING_CALL.test(read)) found.push(read);
	}
	return found;
}

/** Every toast in one client file: its line and each message it can say. */
/** @param {string} text @returns {{ line: number, messages: string[] }[]} */
export function clientToasts(text) {
	/** @type {{ line: number, messages: string[] }[]} */
	const found = [];
	for (const { pattern, at } of DOORS) {
		for (const call of text.matchAll(pattern)) {
			const open = call.index + call[0].length - 1;
			const given = argumentsAt(text, open)[at];
			if (given === undefined) continue;
			const line = text.slice(0, call.index).split('\n').length;
			found.push({ line, messages: messagesOf(given) });
		}
	}
	return found;
}

/** Python string literals in a piece of source, f-strings with their `{...}` written `${...}`. */
/** @param {string} source @returns {string[]} */
function pythonLiterals(source) {
	/** @type {string[]} */
	const found = [];
	for (const one of source.matchAll(/(?<!\w)(f?)("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g)) {
		const body = one[2].slice(1, -1);
		found.push(one[1] ? body.replace(/\{([^{}]+)\}/g, '${$1}') : body);
	}
	return found;
}

/**
 * The server's toast sentences in one router: every literal a `said` is set from, and whether the
 * answer it goes into carries the same line as pieces.
 */
/** @param {string} text @returns {{ line: number, message: string, pieces: boolean }[]} */
export function serverToasts(text) {
	/** @type {{ line: number, message: string, pieces: boolean }[]} */
	const found = [];
	for (const set of text.matchAll(/\bsaid\s*(?:\+=|=)\s*/g)) {
		const from = set.index + set[0].length;
		const end = text[from] === '(' ? closing(text, from) + 1 : text.indexOf('\n', from);
		const source = text.slice(from, end === -1 ? text.length : end);
		const after = text.slice(
			end,
			text.indexOf('\n\n', end) === -1 ? text.length : text.indexOf('\n\n', end)
		);
		const line = text.slice(0, set.index).split('\n').length;
		const pieces = /\bpieces\s*=/.test(after) || /\bpieces\s*=/.test(source);
		for (const message of pythonLiterals(source)) found.push({ line, message, pieces });
	}
	return found;
}
