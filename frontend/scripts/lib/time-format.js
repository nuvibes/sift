// What `check_one_time_format.js` counts, as a function of one file's code, so the unit suite can
// hand it lines and watch it refuse them without planting anything in the source tree.
//
// The gate's own header says what each shape is and why; this is only the reading of them.

/** Each shape, with the words the gate prints beside a line it found. */
const SHAPES = [
	{ what: 'a locale date or time method', find: /\.toLocale(?:Date|Time)String\s*\(/g },
	{ what: "a Date's fixed-English string", find: /\.to(?:Date|Time|UTC)String\s*\(/g },
	{
		what: 'an Intl date formatter',
		find: /\bIntl\s*\.\s*(?:DateTimeFormat|RelativeTimeFormat)\b/g
	},
	{
		what: "a Date's toLocaleString",
		find: /\bnew Date\((?:[^()]|\([^()]*\))*\)\s*\.toLocaleString\s*\(/g
	},
	{
		what: 'toLocaleString with a date or time option',
		find: /\.toLocaleString\s*\([^)]*\b(?:dateStyle|timeStyle|weekday|month|day|hour|minute|timeZone)\s*:/g
	},
	{
		what: 'a date built by hand',
		find: /\$\{[^}]*\.get(?:UTC)?(?:FullYear|Month|Date|Hours|Minutes|Seconds)\(\)/g
	},
	/* A rung of a relative ladder: a number with "ago" after it ("${minutes}m ago", "3 hours ago"),
	   or "in" before one with a unit after ("starts in ${hours}h"). The number is what makes it a
	   rung. "Asked longer ago" is the name of a filter bucket, not a moment being said. */
	{
		what: 'a second ladder of relative words',
		find: /(?:\}|\d)\s?(?:[a-z]+\s)?ago\b/g
	},
	{
		what: 'a second ladder of relative words',
		find: /\bin \$\{[^}]*\}\s?(?:s|m|h|d|min|mins|minutes|hr|hrs|hours|days)\b/g
	}
];

/**
 * Names the code bound to a Date, so `when.toLocaleString()` is caught as well as the literal.
 *
 * @param {string} code
 * @returns {Set<string>}
 */
function datesNamedIn(code) {
	/** @type {Set<string>} */
	const names = new Set();
	for (const found of code.matchAll(/\b(?:const|let|var)\s+(\w+)\s*=\s*new Date\(/g)) {
		names.add(found[1]);
	}
	for (const found of code.matchAll(/\b(\w+)\s*:\s*Date\b/g)) names.add(found[1]);
	return names;
}

/**
 * @param {string} text
 * @param {number} index
 */
function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

/**
 * Every line of `code` (comments already taken out) that writes a date, as `{ line, what }`,
 * in line order, one entry per line however many shapes it holds.
 *
 * @param {string} code
 * @returns {{ line: number, what: string }[]}
 */
export function datesWrittenIn(code) {
	/** @type {Map<number, string>} */
	const found = new Map();
	for (const shape of SHAPES) {
		for (const match of code.matchAll(shape.find)) {
			found.set(lineOf(code, match.index), shape.what);
		}
	}
	for (const name of datesNamedIn(code)) {
		const bound = new RegExp(String.raw`\b${name}\s*\.toLocaleString\s*\(`, 'g');
		for (const match of code.matchAll(bound)) {
			found.set(lineOf(code, match.index), "a Date's toLocaleString");
		}
	}
	return [...found].sort((a, b) => a[0] - b[0]).map(([line, what]) => ({ line, what }));
}
