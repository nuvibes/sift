// A settings pane draws a line only BETWEEN two rows, and the group heading draws the only line
// between two groups.
//
// ## The failure this is for
//
// A row that draws a line under itself, followed by a group heading that draws a line over itself,
// is two hairlines a few dozen pixels apart with nothing between them: a double rule, on every
// group boundary of every pane. It comes from two components each doing the right thing alone.
//
// ## The rule, as a stylesheet can hold it
//
// A hairline across a settings pane (a 1px solid border on an element's top or bottom edge) is
// drawn in one of three ways, and nothing else:
//
//   - on the START edge of an element, under a sibling combinator (`+` or `~`), so it is only ever
//     drawn when something comes before it: the row primitives' line between two rows;
//   - on the END edge of an element whose last one is reset to no line in the same file
//     (`li:last-child { border-block-end: 0 }`): the same shape, written the other way round;
//   - on a table's cells, where the table owns its own lines.
//
// Anything else is a line that is drawn whether or not a row follows it, which is how a pane ends
// up with two. The group heading's own line is `SectionHeading`'s, drawn through `Separator`
// (a filled element, not a border), and is not a border this reads.
//
// ## What this cannot see
//
// Distance. It reads stylesheets, not a laid-out pane, so it holds the SHAPE that makes a double
// rule impossible rather than measuring one. The rendered proof is the pane test beside
// `ActionRow` (`row-lines.svelte.test.ts`), which walks a pane with the compiled stylesheets in
// place and fails on two lines with no row between them.
//
// Run: node scripts/check_row_lines.js

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { everySvelteFile, fromSource, SOURCE, withoutComments } from './lib/tree.js';

const PANES = join(SOURCE, 'lib', 'settings-ui');
const PRIMITIVES = [
	join(SOURCE, 'lib', 'components', 'common', 'LabelledRow.svelte'),
	join(SOURCE, 'lib', 'components', 'common', 'SectionHeading.svelte')
];

/**
 * Files whose hairlines are not a pane's, with the reason.
 *
 * Each entry must keep excusing something (the file exists and draws at least one line this gate
 * would otherwise refuse), or the gate fails: an entry that changes no outcome cannot be told from
 * one that does.
 */
const NOT_A_PANE = {
	'lib/settings-ui/Appearance.svelte':
		'the sidebar list draws its divider as a row of its own, a line with a word on it, which IS the line between two halves of that list'
};

/** How many files must be read before an answer is believed. */
const AT_LEAST = 20;

const HAIRLINE_PROPERTY =
	/^(border-block-start|border-block-end|border-top|border-bottom|border-block)$/;

/** A declaration that draws a thin solid line. */
function drawsHairline(property, value) {
	return (
		HAIRLINE_PROPERTY.test(property) && /\bsolid\b/.test(value) && /(^|\s)(1px|thin)\b/.test(value)
	);
}

/** A declaration that takes the line away again. */
function clearsLine(value) {
	return /^(0|none|0px)\b/.test(value.trim());
}

/**
 * The rules of one stylesheet as `{ selector, property, value }`, innermost blocks only, so a rule
 * inside `@media` is read with its own selector rather than the at-rule's.
 */
export function declarationsOf(css) {
	const found = [];
	for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
		const selector = rule[1].trim();
		for (const line of rule[2].split(';')) {
			const at = line.indexOf(':');
			if (at < 0) continue;
			const property = line.slice(0, at).trim();
			const value = line.slice(at + 1).trim();
			if (property) found.push({ selector, property, value });
		}
	}
	return found;
}

/** A selector list split at its top-level commas, so `:is(a, b)` stays one selector. */
function selectorsIn(list) {
	const parts = [];
	let depth = 0;
	let from = 0;
	for (let at = 0; at < list.length; at += 1) {
		if (list[at] === '(') depth += 1;
		else if (list[at] === ')') depth -= 1;
		else if (list[at] === ',' && depth === 0) {
			parts.push(list.slice(from, at).trim());
			from = at + 1;
		}
	}
	parts.push(list.slice(from).trim());
	return parts;
}

/** Why one hairline declaration is allowed, or null when it is the fault. */
export function allowed(declaration, all) {
	const { selector, property } = declaration;
	for (const one of selectorsIn(selector)) {
		const table = /(^|[\s>])(th|td)(\[[^\]]*\])?(:[\w-]+(\([^)]*\))?)*$/.test(one);
		if (table) continue;
		const between = /[+~]/.test(one);
		const start = /(block-start|top)$/.test(property);
		if (start && between) continue;
		if (!start && property !== 'border-block') {
			const last = all.some(
				(other) =>
					/:last-(child|of-type)/.test(other.selector) &&
					other.selector.replace(/:last-(child|of-type)/g, '').trim() === one &&
					(other.property === property || other.property === 'border') &&
					clearsLine(other.value)
			);
			if (last) continue;
		}
		return null;
	}
	return 'ok';
}

/*
 * The reading, proved against stylesheets whose answer is known before it is used on anything. A
 * reader that matched nothing would report a clean tree for ever.
 */
const KNOWN = [
	['.row { border-block-end: 1px solid var(--sift-line); }', 1],
	['.row { border-top: 1px solid red; }', 1],
	[':global(.ruled-row) + .row { border-block-start: 1px solid var(--sift-line); }', 0],
	[
		':global(:is(.a, :has(> .a))) + .row, :global(:is(.a, .b) + *) > .row { border-top: 1px solid x; }',
		0
	],
	[':global(:is(.a, .b)) .row { border-block-start: 1px solid x; }', 1],
	['li { border-block-end: 1px solid x; } li:last-child { border-block-end: 0; }', 0],
	['th, td { border-bottom: 1px solid x; }', 0],
	['.box { border: 1px solid x; }', 0],
	['@media (min-width: 1px) { .row { border-bottom: 1px solid x; } }', 1],
	['.row { border-block-end: 2px solid x; }', 0]
];
for (const [css, want] of KNOWN) {
	const all = declarationsOf(css);
	const got = all.filter((one) => drawsHairline(one.property, one.value) && !allowed(one, all));
	if (got.length !== want) {
		console.error(
			`check_row_lines: the reading found ${got.length} fault(s) in ${css}, not ${want}.`
		);
		process.exit(2);
	}
}

const files = [...(await everySvelteFile(PANES)), ...PRIMITIVES];
const faults = [];
const excusing = new Set();
let read = 0;

for (const path of files) {
	read += 1;
	const source = await readFile(path, 'utf8');
	const where = fromSource(path);
	const style = source.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1];
	if (!style) continue;
	const all = declarationsOf(withoutComments(style));
	const lines = all.filter((one) => drawsHairline(one.property, one.value) && !allowed(one, all));
	if (lines.length === 0) continue;
	if (where in NOT_A_PANE) {
		excusing.add(where);
		continue;
	}
	for (const one of lines)
		faults.push(`${where}: ${one.selector} { ${one.property}: ${one.value} }`);
}

const inert = Object.keys(NOT_A_PANE).filter((name) => !excusing.has(name));
if (inert.length > 0) {
	console.error(
		'check_row_lines: these are named in NOT_A_PANE and excuse nothing (the file has gone, or it\n' +
			`draws no line this gate refuses):\n  ${inert.join('\n  ')}\n\nRemove them.`
	);
	process.exit(1);
}

if (read < AT_LEAST) {
	console.error(
		`check_row_lines: only ${read} components were read, and there should be at least ${AT_LEAST}.\n` +
			'The walk found nothing to check, which is not the same as nothing being wrong.'
	);
	process.exit(1);
}

if (faults.length > 0) {
	console.error(
		`\nA line on a settings pane that is not drawn between two rows (${faults.length}):\n\n  ` +
			faults.join('\n  ') +
			'\n\n  A row draws its line on its START edge under a sibling combinator\n' +
			'  (`:global(.ruled-row) + .row`), so the first row of a group draws none and its last\n' +
			"  row leaves nothing under itself. The group heading's hairline is the only line between\n" +
			'  two groups: use `SettingGroup` or `SectionHeading` rather than a border of your own.\n'
	);
	process.exit(1);
}

console.log(`row lines: ${read} components read, every line is drawn between two rows`);
