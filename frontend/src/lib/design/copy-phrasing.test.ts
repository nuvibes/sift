/*
 * The phrasing check: the shapes of a sentence a person would not write, found in the strings the
 * client puts on screen and counted per file by `scripts/check_vocabulary.js`.
 *
 * Each case plants a sentence of a shape the check refuses into a component, reads it the way the
 * gate reads the tree, and proves the check finds it; then reads the rewrite and proves it passes.
 */
import { describe, expect, it } from 'vitest';

import { copyIn } from '../../../scripts/lib/copy.js';
import { compare } from '../../../scripts/check_vocabulary.js';
import { CHECKS, offences } from '../../../scripts/lib/vocabulary.js';

/** Every phrasing hit in one component, read the way the gate reads a file. */
const hits = (markup: string) =>
	copyIn(markup, 'lib/Planted.svelte').flatMap((one: { text: string }) =>
		offences('phrasing', one.text, 'lib/Planted.svelte').map((hit) => hit.found)
	);

/** [a sentence of the refused shape, the same sentence rewritten] */
const PAIRS: [string, string][] = [
	[
		'Made inside the folder you are in, on your disk and in your library together.',
		"Sift creates it inside the folder you're in, both on your disk and in your library."
	],
	['Press one again to take it out.', 'Press one again to remove it.'],
	['How it goes out', 'Start the swap'],
	[
		'Restricted means never. Nothing shared later undoes it.',
		'A guest set to Restricted never sees this, even if you later share a folder or tag that includes it.'
	],
	[
		'Other names they are known by. Any of them finds them.',
		'Other names this person goes by. Searching for any of them finds this person.'
	],
	[
		'Ticked on its own for anyone a stash-box that files its creators as people knows.',
		'Sift ticks this for you when a stash-box that lists creators as people knows this person.'
	],
	[
		'Sift creates a library beside this one with the folders it matched and opens it and Stash comes across once the first scan of that library finishes so its files are there to fill in.',
		"Sift creates a library in the same location as this one with the folders it matched, and opens it. Stash comes across once that library's first scan finishes."
	]
];

describe('the phrasing check finds the sentence shapes a person would not write', () => {
	it('is a ratcheted check, counted per file with the others', () => {
		expect(CHECKS).toContain('phrasing');
	});

	for (const [shipped, rewritten] of PAIRS) {
		it(`finds "${shipped.slice(0, 40)}" planted in a component, and passes its rewrite`, () => {
			expect(hits(`<p>${shipped}</p>`).length).toBeGreaterThan(0);
			expect(hits(`<p>${rewritten}</p>`)).toEqual([]);
		});
	}

	it('reads a planted string through a copy attribute as well as between tags', () => {
		expect(hits(`<Button label="Take them out" />`)).toEqual(['Take them out']);
	});

	it('turns a planted string into a rise the ratchet refuses', () => {
		const planted = hits('<p>Press one again to take it out.</p>').length;
		expect(compare({ 'lib/Planted.svelte': planted }, {}).rose).toEqual([
			'lib/Planted.svelte: 1, recorded 0'
		]);
	});

	it('leaves 25 words in one sentence alone and finds the 26th', () => {
		const words = (n: number) => Array.from({ length: n }, () => 'word').join(' ');
		expect(offences('phrasing', `${words(25)}.`)).toEqual([]);
		expect(offences('phrasing', `${words(26)}.`)).toHaveLength(1);
		expect(offences('phrasing', `${words(20)}. ${words(20)}.`)).toEqual([]);
	});
});
