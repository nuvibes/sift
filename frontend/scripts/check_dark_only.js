// Every theme that ships is a DARK one, and no component knows which.
//
// Sift offers several backgrounds, accents and typeface pairings. What this protects is two rules:
//
//   1. NO LIGHT THEME. Not because one is unwelcome (the token layer exists precisely so one could
//      be authored), but because a second set of colours that ships before anyone has designed for
//      it is the half-maintained one somebody eventually turns on and finds unreadable. Every
//      combination Sift ships is dark, and every one of them is measured (see
//      `src/lib/design/contrast.test.ts`).
//
//   2. NO COMPONENT BRANCHES ON THE THEME. A theme is applied by stamping `data-base`, `data-accent`
//      and `data-face` on the document and swapping the primitive layer underneath the semantic
//      names. The moment one component writes its own rule for one of those attributes, the theme
//      stops being a value swap and becomes a fork, and the corner nobody rendered in the other
//      combinations is the corner that is wrong.
//
// So: those three attributes are legitimate, in `src/app.css` and nowhere else. `data-theme` is not
// used at all and stays refused, along with a `.light` class and a `prefers-color-scheme: light`
// block, which are the two ways a light theme actually gets written.
//
// The dark side of `prefers-color-scheme` is allowed and is not a second theme: it is the app
// saying what it already is, so a browser draws its own scrollbars and form controls to match.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const SRC = join(root, 'src');

const GENERATED = join(SRC, 'lib/generated');
const LOOKS_AT = new Set(['.svelte', '.css', '.ts', '.js']);

// The one file allowed to select on a theme, because it is the file the themes live in.
const TOKEN_FILE = join(SRC, 'app.css');

// And the gates, which name those attributes in order to MEASURE the themes: `contrast.test.ts`
// resolves all twelve combinations by matching exactly these selectors out of the token file. A test
// renders nothing, so it cannot fork anything; failing on the file whose job is to check the rule is
// how a gate gets deleted rather than fixed.
const measuresRatherThanRenders = (path) => path.endsWith('.test.ts');

// The three attributes a theme is applied with. Anywhere but the token file, a rule keyed on one of
// these is a component deciding for itself what a theme means.
const THEME_ATTRIBUTES = /\[data-(?:base|accent|face)[^\]]*\]/i;

const PATTERNS = [
	{
		// The media query, in the light direction only.
		test: /prefers-color-scheme\s*:\s*light/i,
		why: 'a light-theme branch',
		anywhere: true
	},
	{
		// A light theme by class, and the attribute name that is deliberately not the one Sift uses.
		// `color-scheme: dark` is a declaration, not a selector, so it does not match.
		test: /\[data-theme[^\]]*\]|(?:^|[\s,{>~+])\.(?:light|theme-light|light-mode)\b/i,
		why: 'a theme selector',
		anywhere: true
	},
	{
		// The real ones, outside the token layer.
		test: THEME_ATTRIBUTES,
		why: 'a component branching on the theme instead of reading a token',
		anywhere: false
	}
];

async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (path.startsWith(GENERATED)) continue;
		if (entry.isDirectory()) yield* walk(path);
		else yield path;
	}
}

const offences = [];
let looked = 0;

for await (const path of walk(SRC)) {
	if (![...LOOKS_AT].some((ext) => path.endsWith(ext))) continue;
	looked += 1;

	const text = await readFile(path, 'utf8');
	const isTokenFile = path === TOKEN_FILE;
	for (const [index, line] of text.split('\n').entries()) {
		for (const { test, why, anywhere } of PATTERNS) {
			if (!anywhere && (isTokenFile || measuresRatherThanRenders(path))) continue;
			if (test.test(line)) {
				offences.push(`${relative(root, path)}:${index + 1}  ${why}  ${line.trim()}`);
			}
		}
	}
}

// A gate that read nothing would pass, silently, forever. Refuse to be that.
if (looked === 0) {
	console.error('check_dark_only: found no files to read. The path it walks has moved.');
	process.exit(2);
}

if (offences.length > 0) {
	console.error('A theme is being decided somewhere other than the token layer:\n');
	for (const offence of offences) console.error(`  ${offence}`);
	console.error(
		'\nEvery theme Sift ships is dark, and no component knows which one is on. A base, an accent ' +
			'or a pairing is a set of values in src/app.css that the semantic tokens are pointed at; a ' +
			'rule keyed on [data-base] in a component is that component forking, and the corner nobody ' +
			'looked at in the other eleven combinations is the one that comes out wrong. A light theme ' +
			'is meant to be possible later, authored the same way, which is what makes it possible.'
	);
	process.exit(1);
}

console.log(`ok   every theme dark and decided in one file, ${looked} files checked`);
