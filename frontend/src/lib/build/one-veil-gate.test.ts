/** What the one-veil gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_one_veil.js` holds the dimmed sheet, the drop offer and the withheld face to the
 * one component that draws each.
 */

import { describe, expect, it } from 'vitest';

import { handDrawnVeilsIn } from '../../../scripts/lib/one-veil.js';

const A_SCREEN = 'lib/components/entity/EntityCard.svelte';
const faults = (code: string, where = A_SCREEN) =>
	handDrawnVeilsIn(code, where).map((one: { what: string }) => one.what);

describe('the one-veil gate', () => {
	it.each([
		['the veil class on an element', '<span class="veil"><Icon /></span>', 'the class `veil`'],
		['the veil class among others', '<div class="face veil wide"></div>', 'the class `veil`'],
		['a scoped rule for the class', '.veil {\n\tposition: absolute;\n}', 'the class `veil`'],
		['the veil ground', '.sheet { background: var(--sift-overlay); }', "a veil's ground"],
		['a veil layer', '.x { z-index: var(--z-dialog-veil); }', "a veil's layer"],
		['the veil blur behind', '.x { backdrop-filter: blur(var(--blur-veil)); }', "the veil's blur"],
		['the drop veil', '.x { background: var(--sift-drop-veil); }', 'the drop veil']
	])('refuses %s', (_name, code, what) => {
		expect(faults(code)).toEqual([expect.stringContaining(what)]);
	});

	it('refuses the veil class written as a directive', () => {
		expect(faults('<div class:veil={shut}></div>')).toEqual([
			expect.stringContaining('the class `veil`')
		]);
	});

	it('refuses the drop layer outside the drop offer', () => {
		expect(faults('.x { z-index: var(--z-drop-overlay); }')).toEqual([
			expect.stringContaining('the drop veil')
		]);
	});

	it.each([
		['a class that only begins the same', '<div class="veiled"></div>'],
		['a rule for a longer name', '.veil-row { color: red; }'],
		['the veil blur on the element itself', '.x::before { filter: blur(var(--blur-veil)); }'],
		['the scrim under controls over media', '.bar { background: var(--sift-scrim); }'],
		['the Veil component', '<Veil label="Close" onclose={close} />']
	])('lets %s through', (_name, code) => {
		expect(faults(code)).toEqual([]);
	});

	it('lets each owner say what it owns', () => {
		expect(faults('<div class="veil"></div>', 'lib/components/common/Modal.svelte')).toEqual([]);
		expect(
			faults('style:z-index="var(--z-{layer}-veil)"', 'lib/components/common/Veil.svelte')
		).toEqual([]);
		expect(
			faults('.o { background: var(--sift-drop-veil); }', 'lib/components/common/DropOffer.svelte')
		).toEqual([]);
	});
});
