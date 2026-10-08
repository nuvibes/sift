// What `check_menu_groups.js` refuses, as a function of one file's code (comments already taken
// out), so the unit suite can hand it lines and watch it refuse them without planting anything in
// the source tree.
//
// Four shapes, each a menu that is not divided the one way every menu is. A short menu is held to
// its parts as a long one is: two rows of different kinds are two parts, and a line divides them.
//
// 1. A hand-placed line. `ContextMenuGroup` draws the line in front of a group and nothing else
//    does, so a `<ContextMenuSeparator` written outside the primitives is a line somebody placed.
// 2. A menu written as markup whose rows are of two kinds, in a file that draws no
//    `<ContextMenuGroup`: a row that destroys (`destructive` on a `ContextMenuItem`) beside one
//    that does not, or a setting (a `ContextMenuItem` given `checked`, a checkbox or radio row of
//    the library's, or `RatingChoices`) beside a row that acts. A row whose `destructive` is worked
//    out as it is drawn (`destructive={one.destructive}`) stands for rows of both kinds.
// 3. A long menu written as markup with no groups: more than `MOST_UNGROUPED` rows in a file that
//    draws no `<ContextMenuGroup`, whatever their kinds, since a menu that long has parts.
// 4. A declared list with no groups: two or more verbs pushed onto one `Verb[]` (or written into
//    one), any of which names no `group` and is not `destructive` (a destructive verb is placed
//    last by being destructive), whatever the list's length. A verb handed over by name or by a
//    call is not read here: its own literal is, where it is written.

/** How many rows a markup menu may have with no group, when its rows are all of one kind. */
export const MOST_UNGROUPED = 5;

/** The fewest verbs a declared list has before each must say which part it belongs to. */
export const FEWEST_PARTED = 2;

/** The rows of one kind that make a setting rather than an act: a checkbox or a radio row. */
const SETTING_ROW = /<(?:RatingChoices|ContextMenu\.(?:CheckboxItem|RadioItem|RadioGroup))\b/g;

/**
 * The attributes of the tag that opens at `open`, up to the `>` that closes it, braces and strings
 * skipped (an attribute's expression can hold a `>`: `onselect={() => go()}`).
 *
 * @param {string} code
 * @param {number} open index of the `<`
 * @returns {string}
 */
function tagAt(code, open) {
	let depth = 0;
	let quote = '';
	for (let at = open + 1; at < code.length; at++) {
		const char = code[at];
		if (quote) {
			if (char === '\\') at++;
			else if (char === quote) quote = '';
			continue;
		}
		if (char === "'" || char === '"' || char === '`') quote = char;
		else if (char === '{') depth++;
		else if (char === '}') depth--;
		else if (char === '>' && depth === 0) return code.slice(open, at + 1);
	}
	return code.slice(open);
}

/**
 * What one `ContextMenuItem` row is: one that destroys, a setting, one that acts, or one whose
 * `destructive` is decided as it is drawn and so may be either.
 *
 * @param {string} tag
 * @returns {'destroys' | 'setting' | 'acts' | 'either'}
 */
function kindOf(tag) {
	const destructive = /\sdestructive(?:\s*=\s*\{([^}]*)\})?(?=[\s/>])/.exec(tag);
	if (destructive) {
		const value = (destructive[1] ?? 'true').trim();
		if (value === 'true') return 'destroys';
		if (value !== 'false') return 'either';
	}
	if (/\s(?:checked|bind:checked)(?=[\s=/>])/.test(tag)) return 'setting';
	return 'acts';
}

/** @param {string} text @param {number} index */
function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

/**
 * The text between the bracket at `open` and the one that closes it, strings skipped.
 *
 * @param {string} code
 * @param {number} open index of the opening bracket
 * @returns {string | null}
 */
function balanced(code, open) {
	const pairs = { '(': ')', '[': ']', '{': '}' };
	/** @type {string[]} */
	const stack = [];
	let quote = '';
	for (let at = open; at < code.length; at++) {
		const char = code[at];
		if (quote) {
			if (char === '\\') at++;
			else if (char === quote) quote = '';
			continue;
		}
		if (char === "'" || char === '"' || char === '`') quote = char;
		else if (char in pairs) stack.push(pairs[/** @type {'(' | '[' | '{'} */ (char)]);
		else if (char === stack[stack.length - 1]) {
			stack.pop();
			if (stack.length === 0) return code.slice(open + 1, at);
		}
	}
	return null;
}

/**
 * The top-level elements of an array literal's inside, split on the commas at depth zero.
 *
 * @param {string} inside
 * @returns {string[]}
 */
function elements(inside) {
	/** @type {string[]} */
	const out = [];
	let depth = 0;
	let quote = '';
	let start = 0;
	for (let at = 0; at < inside.length; at++) {
		const char = inside[at];
		if (quote) {
			if (char === '\\') at++;
			else if (char === quote) quote = '';
			continue;
		}
		if (char === "'" || char === '"' || char === '`') quote = char;
		else if ('([{'.includes(char)) depth++;
		else if (')]}'.includes(char)) depth--;
		else if (char === ',' && depth === 0) {
			out.push(inside.slice(start, at));
			start = at + 1;
		}
	}
	out.push(inside.slice(start));
	return out.map((one) => one.trim()).filter((one) => one !== '');
}

/** A verb handed over by name or by a call: its literal is elsewhere. */
const BY_NAME = /^\.{0,3}[A-Za-z_$][\w$.]*\s*(?:\([\s\S]*\))?$/;

/** @param {string} verb */
const declaresItsPart = (verb) =>
	BY_NAME.test(verb) || /\bgroup\s*:/.test(verb) || /\bdestructive\s*:\s*true\b/.test(verb);

/**
 * Every fault in `code`, as `{ line, what }`, in line order.
 *
 * @param {string} code a file's code with its comments taken out
 * @param {{ primitive?: boolean }} [where] `primitive` for a file in `lib/components/common/`,
 *   the only place a line may be drawn
 * @returns {{ line: number, what: string }[]}
 */
export function menuFaultsIn(code, where = {}) {
	/** @type {{ line: number, what: string }[]} */
	const found = [];

	if (!where.primitive) {
		for (const match of code.matchAll(/<ContextMenuSeparator\b/g)) {
			found.push({
				line: lineOf(code, match.index),
				what: 'a line placed by hand (wrap each part in a ContextMenuGroup instead)'
			});
		}
	}

	const rows = [...code.matchAll(/<ContextMenuItem\b/g)];
	const grouped = /<ContextMenuGroup\b/.test(code);
	if (!grouped && !where.primitive) {
		const kinds = rows.map((row) => ({ at: row.index, kind: kindOf(tagAt(code, row.index)) }));
		for (const setting of code.matchAll(SETTING_ROW)) {
			kinds.push({ at: setting.index, kind: 'setting' });
		}
		kinds.sort((one, other) => one.at - other.at);
		const has = (/** @type {string} */ kind) => kinds.some((one) => one.kind === kind);
		const either = kinds.find((one) => one.kind === 'either');
		const destroys = kinds.find((one) => one.kind === 'destroys');
		const setting = kinds.find((one) => one.kind === 'setting');
		if (either) {
			found.push({
				line: lineOf(code, either.at),
				what: 'a row that may destroy drawn among the others with no ContextMenuGroup'
			});
		} else if (destroys && (has('acts') || has('setting'))) {
			found.push({
				line: lineOf(code, destroys.at),
				what: 'a row that destroys beside the others with no ContextMenuGroup'
			});
		} else if (setting && has('acts')) {
			found.push({
				line: lineOf(code, setting.at),
				what: 'a setting beside a row that acts with no ContextMenuGroup'
			});
		}
	}
	if (rows.length > MOST_UNGROUPED && !grouped) {
		found.push({
			line: lineOf(code, rows[MOST_UNGROUPED].index),
			what: `${rows.length} menu rows and no ContextMenuGroup`
		});
	}

	/** @type {Map<string, { verbs: string[], line: number }>} */
	const lists = new Map();
	/**
	 * @param {string} name
	 * @param {number} line
	 * @returns {{ verbs: string[], line: number }}
	 */
	const listOf = (name, line) => {
		/** @type {{ verbs: string[], line: number }} */
		const had = lists.get(name) ?? { verbs: [], line };
		lists.set(name, had);
		return had;
	};
	for (const match of code.matchAll(/\b([A-Za-z_$][\w$]*)\s*:\s*Verb\[\]\s*=\s*\[/g)) {
		const inside = balanced(code, match.index + match[0].length - 1) ?? '';
		listOf(match[1], lineOf(code, match.index)).verbs.push(...elements(inside));
	}
	for (const [name, list] of lists) {
		const push = new RegExp(String.raw`\b${name.replaceAll('$', '\\$')}\.push\(`, 'g');
		for (const match of code.matchAll(push)) {
			const argument = balanced(code, match.index + match[0].length - 1);
			if (argument !== null) list.verbs.push(...elements(argument));
		}
	}
	for (const [name, list] of lists) {
		if (list.verbs.length < FEWEST_PARTED) continue;
		const loose = list.verbs.filter((verb) => !declaresItsPart(verb));
		if (loose.length > 0) {
			found.push({
				line: list.line,
				what: `${list.verbs.length} verbs in \`${name}\`, ${loose.length} with no group`
			});
		}
	}

	return found.sort((one, other) => one.line - other.line);
}
