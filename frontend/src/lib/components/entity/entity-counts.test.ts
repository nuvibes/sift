import { expect, it } from 'vitest';

import { size } from '$lib/library/facts';
import { tabsFor } from '$lib/entity/related.svelte';
import { cardCells, cellFigure, cellSaid, fitCells, type MeasuredCell } from './entity-counts';

it('leaves out the files, which the card says in words', () => {
	const ids = cardCells('site', 's1', '/sites/s1', { files: 9, people: 4 }).map((one) => one.id);
	expect(ids).toEqual(['people']);
});

it("leaves out a person's Seen with", () => {
	const ids = cardCells('person', 'p1', '/people/p1', { people: 2, tags: 5 }).map((one) => one.id);
	expect(ids).toEqual(['tags']);
});

it('draws no cell for a number the listing does not carry, rather than a dash', () => {
	expect(cardCells('tag', 't1', '/tags/t1', {})).toEqual([]);
});

it('draws no cell for a nought, and keeps the rest in their order', () => {
	const ids = cardCells('person', 'p1', '/people/p1', {
		sites: 1,
		collections: 0,
		photo_sets: 2,
		loops: 0,
		tags: 20
	}).map((one) => one.id);
	expect(ids).toEqual(['sites', 'photo_sets', 'tags']);
});

it("draws the cells in the rail's order on every kind of card, whatever its own tab order", () => {
	const person = cardCells('person', 'p1', '/people/p1', {
		tags: 2,
		collections: 2,
		loops: 5,
		photo_sets: 1,
		sites: 3
	}).map((one) => one.id);
	expect(person).toEqual(['sites', 'collections', 'photo_sets', 'tags', 'loops']);
	const tag = cardCells('tag', 't1', '/tags/t1', {
		sites: 4,
		tags: 1,
		people: 6,
		loops: 2
	}).map((one) => one.id);
	expect(tag).toEqual(['people', 'sites', 'loops']);
});

/*
 * The row is one line at every size a card comes in.
 *
 * The sizes are the card rungs: a card is at least 200, 260, 320 or 400 wide (`justify.ts`
 * CARD_WIDTHS), its body pads by 12 a side and the row is pulled out 8 on its start side, so the
 * row has the card's width less 16. A cell is 16 + 16 + 4 = 36 of padding, glyph and gap plus its
 * figure; real cells are 41-51 wide. The fold is about 30, and the gap between cells 4.
 */
const ROW_OF = (card: number) => card - 2 * 12 + 8;
const GAP = 4;
const PLUS = 30;

function five(width = 45): MeasuredCell[] {
	return ['sites', 'collections', 'photo_sets', 'loops', 'tags'].map((id) => ({ id, width }));
}

function drawn(fitted: { shown: string[]; folded: string[] }, cells: MeasuredCell[]): number {
	const widths = fitted.shown.map((id) => cells.find((one) => one.id === id)?.width ?? 0);
	const parts = [...widths, ...(fitted.folded.length ? [PLUS] : [])];
	return parts.reduce((sum, one) => sum + one, 0) + GAP * Math.max(0, parts.length - 1);
}

it('draws all five where they fit, at the larger sizes', () => {
	for (const card of [320, 400]) {
		expect(fitCells(five(), ROW_OF(card), GAP, PLUS)).toEqual({
			shown: ['sites', 'collections', 'photo_sets', 'loops', 'tags'],
			folded: []
		});
	}
});

it('never draws more than the row holds, at any size, with the widest cells measured', () => {
	for (const card of [200, 260, 320, 400]) {
		for (const width of [41, 45, 51, 66]) {
			const cells = five(width);
			const fitted = fitCells(cells, ROW_OF(card), GAP, PLUS);
			expect(drawn(fitted, cells)).toBeLessThanOrEqual(ROW_OF(card));
			expect([...fitted.shown, ...fitted.folded]).toEqual(cells.map((one) => one.id));
		}
	}
});

it('folds the tail into "+2" on the smallest card', () => {
	expect(fitCells(five(), ROW_OF(200), GAP, PLUS)).toEqual({
		shown: ['sites', 'collections', 'photo_sets'],
		folded: ['loops', 'tags']
	});
});

it('keeps the rail order in what stays, and folds only from the end', () => {
	const fitted = fitCells(five(51), ROW_OF(260), GAP, PLUS);
	expect(fitted.shown).toEqual(
		['sites', 'collections', 'photo_sets', 'loops'].slice(0, fitted.shown.length)
	);
	expect(fitted.folded.at(-1)).toBe('tags');
});

it('says the size of the files beside the Files number on a hover card, and nowhere else', () => {
	const tabs = tabsFor('person', 'p1', '/people/p1', {
		files: 1957,
		files_bytes: 23_000_000_000,
		tags: 4
	});
	const files = tabs.find((one) => one.id === 'files')!;
	const tags = tabs.find((one) => one.id === 'tags')!;
	expect(cellFigure(files)).toBe(`1,957 \u00b7 ${size(23_000_000_000)}`);
	expect(cellSaid(files)).toBe(`1,957 files \u00b7 ${size(23_000_000_000)}`);
	expect(cellFigure(tags)).toBe('4');
	expect(tags.size).toBeUndefined();
	const unsized = tabsFor('person', 'p1', '/people/p1', { files: 1957 });
	expect(cellFigure(unsized.find((one) => one.id === 'files')!)).toBe('1,957');
});
