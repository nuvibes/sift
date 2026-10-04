// Every state a primitive draws is drawn on the gallery, and the gallery draws no state it lacks.
//
// ## What this holds
//
// The design gives every primitive the same eight states (rest, hover, focus, pressed, selected,
// disabled, busy, error) and the gallery draws them as a matrix: one `<StateRow of="Name"
// states={[...]}>` per primitive, one cell per state (`routes/design/StateRow.svelte`). A state a
// stylesheet draws and nobody looks at is where the drift hides: two primitives answering the
// pointer in two ways look fine until somebody has them side by side. So:
//
//   1. A row's `of` names a primitive, a row names only the eight states, starts with `rest`, and
//      no primitive has two rows.
//   2. Every state the primitive's stylesheet DECLARES has a cell in its row. What a stylesheet
//      declares is read off its selectors (the list below): the component's own `<style>`, and the
//      rules in `app.css` that name one of the classes its markup writes.
//   3. Every cell is for a state the primitive has: one it declares, one a primitive it composes
//      declares (its `composes:` basis), one `app.css` gives the element it is built on (its
//      `site:<tag>` basis), or focus, which `app.css` draws on anything that can take it.
//   4. A primitive that declares a state and has no row at all is counted, and the count may only
//      fall: a ratchet is how the rows arrive without one change touching every primitive. A file that says `NOT ON THE GALLERY:`
//      (the one toaster the layout draws) is not counted, as in `check_design_entries.js`.
//
// A state inside `:not(...)` is a guard, not a look (`:hover:not(:disabled)` draws no disabled), so
// negations are taken out before the selectors are read.
//
// ## The gallery is a separate repository
//
// It is nested at `routes/design/` and a clone of this repository has none, so the gate says so
// and passes there, as `check_design_entries.js` does.

import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

const PRIMITIVES = join(SOURCE, 'lib', 'components', 'common');
const GALLERY = join(SOURCE, 'routes', 'design');
const APP_CSS = join(SOURCE, 'app.css');

/** The two picture-shaped objects outside the primitives folder that the matrix covers. */
const OUTSIDE = {
	Tile: join(SOURCE, 'lib', 'components', 'Tile.svelte'),
	EntityCard: join(SOURCE, 'lib', 'components', 'entity', 'EntityCard.svelte')
};

const HARNESS = /(Harness|Probe)\.svelte$|\.test\.svelte$/;

/** The written judgement for a primitive the gallery cannot draw. Same spelling as `check_design_entries.js`. */
const DRAWS_NOTHING = 'NOT ON THE GALLERY:';

/** The states, in the matrix's order. */
const STATES = ['rest', 'hover', 'focus', 'pressed', 'selected', 'disabled', 'busy', 'error'];

/** How a selector says it draws a state. Classes are the ones the primitives use for it. */
const DECLARES = {
	hover: /:hover(?![\w-])|\[data-highlighted\]/,
	focus: /:focus(?:-visible|-within)?(?![\w-])/,
	pressed: /:active(?![\w-])/,
	selected:
		/\[aria-(?:selected|pressed|checked)(?:[=\]])|\[data-state=['"]?checked|\.(?:selected|picked|on)(?![\w-])/,
	disabled: /:disabled(?![\w-])|\[aria-disabled|\[data-disabled\]|\.disabled(?![\w-])/,
	busy: /\[aria-busy|\.busy(?![\w-])/,
	error: /\[aria-invalid|\[data-invalid\]|:user-invalid|\.invalid(?![\w-])/
};

/** A selector with every `:not(...)` taken out, nested parentheses and all. */
function withoutNegations(selector) {
	let out = '';
	for (let at = 0; at < selector.length; at += 1) {
		if (selector.startsWith(':not(', at)) {
			let depth = 0;
			let end = at + ':not'.length;
			for (; end < selector.length; end += 1) {
				if (selector[end] === '(') depth += 1;
				else if (selector[end] === ')') {
					depth -= 1;
					if (depth === 0) break;
				}
			}
			at = end;
			continue;
		}
		out += selector[at];
	}
	return out;
}

/** Every selector in a stylesheet's text (a rule's prelude), at-rules left out. */
function selectorsIn(css) {
	return [...css.matchAll(/([^{}]+)\{/g)]
		.map((one) => one[1].trim())
		.filter((prelude) => prelude.length > 0 && !prelude.startsWith('@'));
}

/** The states a list of selectors declares. */
function statesDeclaredBy(selectors) {
	const found = new Set();
	for (const selector of selectors) {
		const read = withoutNegations(selector);
		for (const [state, pattern] of Object.entries(DECLARES)) {
			if (pattern.test(read)) found.add(state);
		}
	}
	return found;
}

/** A component's own stylesheet, comments out. */
function ownStyle(source) {
	const code = withoutComments(source, { markup: false });
	return [...code.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((one) => one[1]).join('\n');
}

/** The classes a component's markup writes: `class="a b"` literals and `class:name` directives. */
function markupClasses(source) {
	const markup = source.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g, '');
	const names = new Set();
	for (const one of markup.matchAll(/\bclass="([^"]*)"/g)) {
		for (const word of one[1].replace(/\{[^}]*\}/g, ' ').split(/\s+/)) {
			if (/^[a-z][\w-]*$/.test(word)) names.add(word);
		}
	}
	for (const one of markup.matchAll(/\bclass:([a-z][\w-]*)/g)) names.add(one[1]);
	return names;
}

/** The `design` declaration's basis, read as text: the primitives it composes and its site tag. */
function basisOf(source) {
	const basis = source.match(/basis:\s*'([^']*)'/)?.[1] ?? '';
	const composes = (basis.match(/composes:([A-Za-z,]+)/)?.[1] ?? '').split(',').filter(Boolean);
	const tag = basis.match(/site:<([a-z]+)/)?.[1] ?? null;
	return { composes, tag };
}

/** A selector list split at its own commas, never at one inside `:is(...)` or `:where(...)`. */
function topLevel(list) {
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

const appCss = withoutComments(await readFile(APP_CSS, 'utf8'));
const appSelectors = selectorsIn(appCss).flatMap(topLevel);

/** The app.css selectors that name one of these classes. */
function appSelectorsFor(classes) {
	return appSelectors.filter((selector) =>
		[...classes].some((name) => new RegExp(`\\.${name}(?![\\w-])`).test(selector))
	);
}

/** The app.css selectors that dress a site element by its tag. */
function appSelectorsForTag(tag) {
	return appSelectors.filter((selector) =>
		new RegExp(`(^|[\\s(,>+~])${tag}(?![\\w-])`).test(selector)
	);
}

/* Every primitive: its declared states, and what it may draw beyond them. */
const primitives = new Map();
const sources = [];
for (const path of await everySvelteFile(PRIMITIVES)) {
	if (HARNESS.test(path)) continue;
	const name = path.replace(/\\/g, '/').split('/').pop().slice(0, -'.svelte'.length);
	sources.push([name, path]);
}
for (const [name, path] of Object.entries(OUTSIDE)) sources.push([name, path]);

for (const [name, path] of sources) {
	const source = await readFile(path, 'utf8');
	const own = selectorsIn(ownStyle(source));
	const declared = statesDeclaredBy([...own, ...appSelectorsFor(markupClasses(source))]);
	const { composes, tag } = basisOf(source);
	const fromTag = tag ? statesDeclaredBy(appSelectorsForTag(tag)) : new Set();
	const exempt = source.includes(DRAWS_NOTHING);
	primitives.set(name, { where: fromSource(path), declared, composes, fromTag, exempt });
}

/** Everything a primitive may draw: its own, its tag's, and what it composes, all the way down. */
function allowed(name, seen = new Set()) {
	const one = primitives.get(name);
	if (!one || seen.has(name)) return new Set();
	seen.add(name);
	const may = new Set([...one.declared, ...one.fromTag, 'rest', 'focus']);
	for (const part of one.composes) for (const state of allowed(part, seen)) may.add(state);
	return may;
}

/* A scan that found nothing would pass, silently, forever. */
if (primitives.size < 40) {
	console.error(
		`\nstate-matrix: found only ${primitives.size} primitives. The folder moved, or this gate reads the wrong one.\n`
	);
	process.exit(1);
}

if (!existsSync(GALLERY)) {
	console.log(
		'state-matrix: the gallery is not in this tree (it is a separate repository), so nothing was measured'
	);
	process.exit(0);
}

/* The rows, from every file of the gallery: `<StateRow of="Name" states={['rest', ...]}`. */
const ROW = /<StateRow\b[^>]*?\bof="([A-Za-z]+)"[^>]*?\bstates=\{\[([^\]]*)\]\}/g;
const complaints = [];
const rows = new Map();
for (const path of await everySvelteFile(GALLERY)) {
	const where = fromSource(path);
	const markup = withoutComments(await readFile(path, 'utf8'), { block: false, line: false });
	for (const one of markup.matchAll(ROW)) {
		const [, name, list] = one;
		const states = [...list.matchAll(/'([a-z]+)'/g)].map((state) => state[1]);
		if (rows.has(name)) {
			complaints.push(`${where}: a second row for ${name}. One primitive, one row.`);
			continue;
		}
		rows.set(name, { where, states });
	}
}

if (rows.size === 0) {
	console.error('\nstate-matrix: no `<StateRow of=... states={[...]}>` anywhere in the gallery.\n');
	process.exit(1);
}

for (const [name, { where, states }] of rows) {
	const primitive = primitives.get(name);
	if (!primitive) {
		complaints.push(`${where}: a row for '${name}', and no primitive is called that.`);
		continue;
	}
	if (states[0] !== 'rest') complaints.push(`${where}: ${name}'s row does not start at rest.`);
	for (const state of states) {
		if (!STATES.includes(state)) {
			complaints.push(
				`${where}: ${name} has a cell for '${state}', which is not one of ${STATES.join(', ')}.`
			);
		}
	}
	for (const state of primitive.declared) {
		if (!states.includes(state)) {
			complaints.push(
				`${primitive.where}: its stylesheet draws ${state}, and its row in ${where} has no ${state} cell.\n` +
					`    Draw it: add '${state}' to the row's states and have the cell put the component in it.`
			);
		}
	}
	const may = allowed(name);
	for (const state of states) {
		if (STATES.includes(state) && !may.has(state)) {
			complaints.push(
				`${where}: ${name}'s row draws ${state}, and ${primitive.where} has no ${state} state.\n` +
					'    A cell for a state the primitive lacks draws the rest state and calls it something else.'
			);
		}
	}
}

/* 4. Declared states and no row. */
const undrawn = [...primitives]
	.filter(([name, one]) => one.declared.size > 0 && !rows.has(name) && !one.exempt)
	.map(([, one]) => `${one.where}  (${[...one.declared].join(', ')})`);
const complaint = await ratchet('state-matrix', 'undrawn', undrawn.length, {
	what: 'primitives whose stylesheet draws a state and whose states the gallery does not draw',
	instead:
		'Instead: give it a row in routes/design/StateMatrix.svelte, one cell per state it draws.',
	offenders: undrawn
});
if (complaint) complaints.push(complaint);

if (complaints.length > 0) {
	console.error('\nEvery state a primitive draws is drawn on the gallery, and nothing more.\n');
	for (const one of complaints) console.error(`  ${one}\n`);
	process.exit(1);
}

console.log(
	`state-matrix: ${rows.size} primitives drawn in every state they have; ${undrawn.length} not yet in the matrix`
);
