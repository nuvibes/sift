/** What the card-fill gate refuses, driven with lines written for the purpose.
 *
 * `scripts/check_card_fill.js` holds every card to the card's light (`--sift-card`) and every
 * other ground in the card tone to a named reason, and the shell's screen to the page's light.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { CARDS, EXCUSED, unlitCardsIn, unlitScreenIn } from '../../../scripts/lib/card-fill.js';
import { withoutComments } from '../../../scripts/lib/tree.js';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (where: string) => withoutComments(readFileSync(resolve(SOURCE, where), 'utf8'));
const faults = (code: string, where: string) =>
	unlitCardsIn(code, where).map((one: { what: string }) => one.what);

const A_CARD = 'lib/components/entity/EntityCard.svelte';
const A_NEW_FILE = 'lib/components/NewThing.svelte';

describe('the card-fill gate', () => {
	it('refuses a card painted in the flat card tone', () => {
		const code =
			'.card {\n\tbackground: var(--sift-surface-2);\n}\n.x { background: var(--sift-card); }';
		expect(faults(code, A_CARD)).toEqual([expect.stringContaining('the flat card tone (`.card`)')]);
	});

	it('refuses a card that never reads the light', () => {
		expect(faults('.card { background: var(--sift-accent-bg); }', A_CARD)).toEqual([
			expect.stringContaining("never wears the card's light")
		]);
	});

	it('refuses the flat card tone in a file on neither list', () => {
		expect(faults('.box {\n\tbackground-color: var(--sift-surface-2);\n}', A_NEW_FILE)).toEqual([
			expect.stringContaining('a file nobody has named')
		]);
	});

	it.each([
		['a card wearing the light', '.card { background: var(--sift-card); }', A_CARD],
		[
			'a card with no edge wearing the fill',
			'.card { background: var(--sift-card-fill); }',
			A_CARD
		],
		[
			'a hover layer over the card tone',
			'.x:hover { background: color-mix(in srgb, currentColor 8%, var(--sift-surface-2)); }',
			A_NEW_FILE
		],
		[
			'an excused file',
			'.drawer { background: var(--sift-surface-2); }',
			'lib/components/common/Drawer.svelte'
		],
		[
			'a specimen in the DESIGN GALLERY',
			'.shape { background: var(--sift-surface-2); }',
			'routes/design/+page.svelte'
		]
	])('lets %s through', (_name, code, where) => {
		expect(faults(code, where)).toEqual([]);
	});

	it('lets a card keep the flat tone for the floating form it names', () => {
		const code =
			'.raised {\n\tbackground: var(--sift-surface-2);\n}\n.raised:not(.floating) { background: var(--sift-card); }';
		expect(faults(code, 'lib/components/common/Panel.svelte')).toEqual([]);
	});

	it('gives every excused file a reason and names no file twice', () => {
		for (const [where, why] of Object.entries(EXCUSED)) expect(why, where).not.toBe('');
		expect(Object.keys(CARDS).filter((where) => where in EXCUSED)).toEqual([]);
	});

	it('passes the tree as it stands, card by card', () => {
		for (const where of Object.keys(CARDS)) expect(faults(read(where), where), where).toEqual([]);
	});

	it("holds the shell's screen to the page's light", () => {
		expect(unlitScreenIn(read('routes/+layout.svelte'))).toEqual([]);
		const cover = '.content:has(:global(.frame.on-a-picture)) {\n\tbackground: var(--sift-bg);\n}';
		expect(unlitScreenIn(`.content {\n\tbackground: var(--background);\n}\n${cover}`)).toEqual([
			expect.stringContaining('--sift-page-fill')
		]);
	});

	it("holds the screen under an entity's blurred cover to the flat canvas", () => {
		const screen = '.content {\n\tbackground: var(--sift-page-fill);\n}';
		expect(unlitScreenIn(screen)).toEqual([expect.stringContaining('blurred cover')]);
		expect(
			unlitScreenIn(
				`${screen}\n.content:has(:global(.frame.on-a-picture)) {\n\tbackground: var(--sift-page-fill);\n}`
			)
		).toEqual([expect.stringContaining('blurred cover')]);
	});
});
