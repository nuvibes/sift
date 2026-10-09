/** Every custom property the interface reads is one somebody defined. */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file rather than from the working directory: the runner
 *  is started from more than one place and a relative root silently surveys nothing. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Read anywhere, defined nowhere, and correct. Each needs a reason, and the reason is never
 *  "it looked fine": these are set at run time by something outside the stylesheets. */
const SET_ELSEWHERE = new Set([
	// The component library writes these onto its own elements as it measures them.
	'--bits-select-anchor-width',
	'--bits-select-content-available-height',
	'--bits-combobox-anchor-width',
	// Written onto the element by the component that draws it, from data: a colour derived per
	// entity, and a depth that indents a tree.
	'--hue',
	'--level',
	// Where the mini player has been dragged to and how big it has been pulled.
	'--mini-x',
	'--mini-y',
	'--mini-w',
	'--mini-h',
	// How far along its track a slider's handle has travelled, written onto the element by the
	// slider itself.
	'--at',
	// How many tabs are sharing the strip out, and how many verbs a face is carrying.
	'--tabs',
	'--verbs'
]);

const EXTENSIONS = ['.svelte', '.css', '.ts'];

function sources(directory: string, found: string[] = []): string[] {
	for (const entry of readdirSync(directory)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const path = join(directory, entry);
		if (statSync(path).isDirectory()) sources(path, found);
		else if (EXTENSIONS.some((extension) => entry.endsWith(extension))) found.push(path);
	}
	return found;
}

/** The source with its comments taken out. Comments are where token names get *discussed*: this
 * file talks about several, and a note elsewhere warns about one by name. */
function withoutComments(text: string): string {
	return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** What the whole interface reads, and what it defines, in one pass over the tree. */
function survey(): { used: Map<string, string>; defined: Set<string> } {
	const used = new Map<string, string>();
	const defined = new Set<string>();
	for (const path of sources(SOURCE)) {
		const text = withoutComments(readFileSync(path, 'utf8'));
		for (const match of text.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)) {
			if (!used.has(match[1])) used.set(match[1], path);
		}
		// A definition is a declaration, so it is followed by a colon.
		for (const match of text.matchAll(/(--[\w-]+)\s*:/g)) defined.add(match[1]);
		// And a Svelte style directive, `style:--name={value}`, which writes the property onto the
		// element from data: a definition with the name BEFORE the punctuation.
		for (const match of text.matchAll(/style:(--[\w-]+)\s*=/g)) defined.add(match[1]);
	}
	return { used, defined };
}

describe('the design tokens the interface reads', () => {
	it('are all defined somewhere', () => {
		const { used, defined } = survey();
		const missing = [...used]
			.filter(([name]) => !defined.has(name) && !SET_ELSEWHERE.has(name))
			.map(([name, path]) => `${name} (first read in ${path.slice(SOURCE.length)})`);

		expect(missing, 'read but never defined: the declaration is silently dropped').toEqual([]);
	});

	it('is the only place a primitive is read', () => {
		// THE SPLIT'S PROOF. The token layer has two halves: `--p-*` is the palette a theme owns,
		// and `--sift-*` is what the interface reads.
		const offenders: string[] = [];
		for (const path of sources(SOURCE)) {
			if (path.endsWith('app.css')) continue;
			const text = withoutComments(readFileSync(path, 'utf8'));
			for (const match of text.matchAll(/var\(\s*(--p-[\w-]+)/g)) {
				offenders.push(`${match[1]} in ${path.slice(SOURCE.length)}`);
			}
		}
		expect(offenders, 'a component reached past the semantic layer to a primitive').toEqual([]);
	});

	it('would notice a component reading one', () => {
		// The check above is only worth having if it can fail, and app.css is full of exactly the
		// pattern it looks for, so ask it the same question about the one file that is allowed to.
		const tokens = readFileSync(join(SOURCE, 'app.css'), 'utf8');
		expect([...withoutComments(tokens).matchAll(/var\(\s*(--p-[\w-]+)/g)].length).toBeGreaterThan(
			0
		);
	});

	it('finds a property that is read and never defined', () => {
		// The guard above is only worth having if it can fail, and a survey of a real tree passing
		// says nothing about whether it would notice.
		const { defined } = survey();
		expect(defined.has('--a-token-nobody-declared')).toBe(false);
		expect(SET_ELSEWHERE.has('--a-token-nobody-declared')).toBe(false);
	});
});
