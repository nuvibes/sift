// What `check_one_estimate_format.js` refuses, as a function of one file's code, so the unit suite
// can hand it lines and watch it refuse them without planting anything in the source tree.

/** A unit of time as a screen writes one after a number. */
const UNIT = String.raw`(?:s|m|h|d|min|mins|minutes?|hr|hrs|hours?|days?)`;

/** Each shape, with the words the gate prints beside a line it found. */
const SHAPES = [
	{
		what: 'a figure of time left',
		find: new RegExp(String.raw`(?:\}|\b\d+(?:\.\d+)?)\s?${UNIT}\s+left\b`, 'g')
	},
	{
		what: 'an estimate worded by hand',
		find: new RegExp(String.raw`\babout \$?\{[^}]*\}\s?(?:to \$?\{|${UNIT}\b)`, 'gi')
	}
];

/**
 * @param {string} text
 * @param {number} index
 */
function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

/**
 * Every line of `code` (comments already taken out) that words an estimate itself, as
 * `{ line, what }`, in line order, one entry per line.
 *
 * @param {string} code
 * @returns {{ line: number, what: string }[]}
 */
export function estimatesWrittenIn(code) {
	/** @type {Map<number, string>} */
	const found = new Map();
	for (const shape of SHAPES) {
		for (const match of code.matchAll(shape.find)) {
			found.set(lineOf(code, match.index), shape.what);
		}
	}
	return [...found].sort((a, b) => a[0] - b[0]).map(([line, what]) => ({ line, what }));
}
