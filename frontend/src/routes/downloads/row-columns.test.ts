import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { checkColumns } from '$lib/components/common/DataRows.svelte';
import { DOWNLOAD_ACTIONS, PHONE_ACTIONS, downloadColumns } from './DownloadRow.svelte';

/*
 * EVERY DOWNLOAD ROW'S COLUMNS STAND AT THE SAME X, AND THE LIST DECLARES THEM.
 *
 * The state, the size and the moment are three columns a person reads down, not one cell stacked
 * two lines deep at the row's end; the fix stands against the row's actions rather than floating
 * between the name and a band of hidden buttons. The list declares the tracks once (`DataRows`) and
 * every row lays its cells into them, so what one row holds cannot move where a column is.
 */

const source = readFileSync('src/routes/downloads/DownloadRow.svelte', 'utf8');
const markup = source.slice(source.lastIndexOf('</script>'), source.indexOf('<style>'));
const wide = downloadColumns(false);

describe('the download list declares its columns', () => {
	it('reads the state, the size and the moment as three columns, then the fix', () => {
		expect(wide.map((column) => column.id)).toEqual([
			'pick',
			'mark',
			'name',
			'status',
			'size',
			'when',
			'fix'
		]);
	});

	it('puts figures against the end of their column so they line up on the last digit', () => {
		const align = Object.fromEntries(wide.map((column) => [column.id, column.align ?? 'start']));
		expect(align.size).toBe('end');
		expect(align.when).toBe('end');
		expect(align.fix).toBe('end');
	});

	it('sizes every track by a length, never by what a row holds', () => {
		expect(() => checkColumns(wide, DOWNLOAD_ACTIONS)).not.toThrow();
		expect(() => checkColumns(downloadColumns(true), DOWNLOAD_ACTIONS)).not.toThrow();
	});

	it('keeps the fix as the last data column, standing against the row actions', () => {
		expect(wide.at(-1)?.id).toBe('fix');
	});

	it('hands its cells to the list rather than laying out a grid of its own', () => {
		expect(markup).toMatch(/cells=\{narrow/);
		expect(source).not.toMatch(/grid-template-columns/);
		expect(markup).not.toMatch(/trailing=/);
	});

	it('under the floor, carries the facts and the fix under the name in the same order', () => {
		expect(downloadColumns(true).map((column) => column.id)).toEqual(['pick', 'mark', 'name']);
		const narrow = markup.slice(markup.indexOf('{#snippet cellNameNarrow()}'));
		const order = ['cellStatus()', 'cellSize()', 'cellWhen()', 'cellFix()'].map((cell) =>
			narrow.indexOf(cell)
		);
		expect(order.every((at) => at > 0)).toBe(true);
		expect([...order].sort((one, other) => one - other)).toEqual(order);
	});
});

describe("the download list at a phone's width", () => {
	it('declares an actions track of the three dots and the arrow alone', () => {
		expect(() => checkColumns(downloadColumns(true), PHONE_ACTIONS)).not.toThrow();
		expect(PHONE_ACTIONS).not.toContain('--control-height)');
		const page = readFileSync('src/routes/downloads/+page.svelte', 'utf8');
		expect(page).toMatch(/actions=\{narrow\.yes \? PHONE_ACTIONS : DOWNLOAD_ACTIONS\}/);
		expect(page).toMatch(/phone=\{narrow\.yes\}/);
	});
});
