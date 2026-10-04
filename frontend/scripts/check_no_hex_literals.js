// A colour is named in one file, or the tokens are decoration.
//
// The token layer is the reason the app can be re-themed at all: swap the primitives and everything
// follows. One hex literal in one component is a corner no theme can reach, and it does not announce
// itself. It looks right, in the one theme it was written against. By the time there is a second
// theme there are forty of them.
//
// So: app.css may name colours. Nothing else may.

import { readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

import { everyFile, FRONTEND, SOURCE } from './lib/tree.js';

const root = FRONTEND;
const SRC = SOURCE;

// The one file that is allowed to know what colour anything is.
const TOKEN_FILE = join(SRC, 'app.css');

// Generated (its contents come from the font packages, not from anyone here), and `.svelte-kit`,
// whose error page carries #222 and #ccc. Both are skipped by the shared walk in `lib/tree.js`.

/*
 * The one place a colour is DATA rather than a decision.
 *
 * Everything this check refuses is refused because a colour written outside the token file is a
 * corner no theme can reach. `titlebar.svelte.ts` converts one colour spelling into another so the
 * desktop shell can be handed the caption-button shade the page is actually drawing, and a test of
 * a converter has to name what goes in and what should come out. Those hexes are arguments to a pure
 * function. Nothing on any screen is that colour because of them.
 *
 * Named one file at a time rather than exempting tests as a class, and that distinction is the
 * point: a test asserting that `--p-warn` is a particular hex WOULD be a second declaration of a
 * colour, free to drift from the real one, and this check should keep catching it.
 */
const ALLOWED = new Set([
	join(SRC, 'lib/shell/titlebar.svelte.test.ts'),
	// The same case, one file along: `lib/theme/accent.ts` derives a whole accent family from one
	// chosen colour, and a test of it has to name a colour going in and say what should come out.
	// The GROUNDS it is measured against are read out of `app.css` rather than written here, which
	// is the half that would otherwise be a second declaration of the palette; what is left is a
	// handful of arguments. Nothing on any screen is one of these colours because of them.
	join(SRC, 'lib/theme/accent.test.ts')
]);

const LOOKS_AT = new Set(['.svelte', '.css', '.ts', '.js']);

// #rgb, #rrggbb, #rrggbbaa. Anchored on a boundary so an id, a URL fragment or a hash in a comment
// does not read as a colour.
const HEX = /(?<![\w&])#[0-9a-fA-F]{3,8}\b/g;

const offences = [];

for (const path of await everyFile(SRC, [...LOOKS_AT])) {
	if (path === TOKEN_FILE || ALLOWED.has(path)) continue;

	const text = await readFile(path, 'utf8');
	for (const [index, line] of text.split('\n').entries()) {
		for (const match of line.matchAll(HEX)) {
			// A colour is 3, 4, 6 or 8 digits. Anything else is not one: most often an id.
			if (![3, 4, 6, 8].includes(match[0].length - 1)) continue;
			offences.push(`${relative(root, path)}:${index + 1}  ${match[0]}  ${line.trim()}`);
		}
	}
}

if (offences.length > 0) {
	console.error('A colour is written where no theme can reach it:\n');
	for (const offence of offences) console.error(`  ${offence}`);
	console.error(
		'\nEvery colour comes from a token in src/app.css. If the one you want is not there, it is ' +
			'a design decision and not a local one: add it there, or use the token that means what ' +
			'you mean.'
	);
	process.exit(1);
}

console.log('ok   no colours outside the token layer');
