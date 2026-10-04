// Every design token a stylesheet asks for is a token that exists.
//
// CSS has no warning for this. A custom property that was never defined is not an error and not a
// console message: the declaration using it becomes "invalid at computed-value time", which means
// the property is thrown away and the element inherits instead. Written into a `font:` shorthand it
// takes the family, the weight and the size with it, so a heading comes out at body size in the
// body face and everything downstream is green: the type check, the unit tests, the build. A
// missing scrim token leaves every control over a picture with no ground at all.
//
// The usual cause is a plausible name for a token the design system does not have. `--font-mono` is
// named in app.css as a thing deliberately left out.
//
// What is checked, and what is deliberately not:
//
//   - a `var(--x)` in any stylesheet must resolve to a `--x:` declared in app.css, OR to one
//     declared in the same file, OR to one the file sets on an element with `style:--x=` in its
//     markup, OR to a fallback the author supplied: `var(--x, 12px)` is a deliberate default and
//     not a mistake.
//   - properties owned by a third-party component library are allowed by prefix. They are set by
//     that library at runtime and are not ours to declare.
//
// What this cannot see: it reads a file at a time, so a property declared ANYWHERE in a file counts
// as resolvable EVERYWHERE in it. A rule reading `--inner`, declared on a card, that also dresses a
// button outside any card resolves nothing there, and its radius is thrown away while every rule
// this gate applies is satisfied.
//
// Telling those apart means knowing which elements a selector can match, which is a question about
// the rendered page rather than about the text, so it is answered where the page exists:
// `e2e/native-chrome.spec.ts` measures a real button on every screen and every settings pane and
// refuses a square corner. It cannot be done statically, so do not add a second static gate for it.

import { readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

import { everyFile, FRONTEND, SOURCE, withoutComments } from './lib/tree.js';

const root = FRONTEND;
const SRC = SOURCE;
const GLOBAL_CSS = join(SRC, 'app.css');

// Set by a component library at runtime, on its own elements. Not ours to declare, and not ours to
// rename either: the names come from the library's own stylesheet contract.
const FOREIGN_PREFIXES = ['--bits-'];

/** Every `--name:` declared in a chunk of text. */
function declaredIn(text) {
	return new Set([...text.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((match) => match[1]));
}

/*
 * Comments blanked, newlines kept: `withoutComments` from `lib/tree.js`, block comments only.
 *
 * A token named in prose is not a token being used, and this file's own documentation names
 * several. Deleting the text outright would put every later line number out by however much was
 * removed, so the comment is replaced by spaces of the same shape instead and the offence reported
 * still points at the real line.
 */
const blankComments = (css) => withoutComments(css, { markup: false, line: false });

const global = declaredIn(blankComments(await readFile(GLOBAL_CSS, 'utf8')));

const offences = [];

for (const path of await everyFile(SRC, ['.svelte', '.css'])) {
	const text = await readFile(path, 'utf8');
	const where = relative(root, path);

	/* Only the style block of a component. A `var(--x)` inside a comment or a string elsewhere in
	   the file is prose, and this gate exists to catch stylesheets rather than to police writing. */
	const styleAt = path.endsWith('.svelte') ? text.indexOf('<style>') : 0;
	if (styleAt < 0) continue;
	const styles = blankComments(text.slice(styleAt));

	// Declared here, or handed to an element from this file's markup with `style:--x=`.
	const local = declaredIn(styles);
	for (const match of text.matchAll(/style:(--[a-z0-9-]+)/gi)) local.add(match[1]);

	for (const match of styles.matchAll(/var\(\s*(--[a-z0-9-]+)\s*([,)])/gi)) {
		const [, name, next] = match;
		if (next === ',') continue; // a fallback was supplied on purpose
		if (global.has(name) || local.has(name)) continue;
		if (FOREIGN_PREFIXES.some((prefix) => name.startsWith(prefix))) continue;
		const line = styles.slice(0, match.index).split('\n').length;
		offences.push(`${where}:${line}  var(${name})`);
	}
}

if (offences.length > 0) {
	console.error('Design tokens that do not exist:\n');
	for (const offence of offences) console.error(`  ${offence}`);
	console.error(
		'\nA custom property that was never defined is not an error in CSS. The declaration using it ' +
			'is discarded and the element inherits instead, so a heading silently comes out at body ' +
			'size and nothing anywhere says so. Define the token in app.css, or use the one that ' +
			'already means what you want.'
	);
	process.exit(1);
}

console.log(`ok   every token resolves (${global.size} defined in app.css)`);
