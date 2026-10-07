/* A page's size is the screen's: never what the page brought back, or a page of tall clips would
 * resize itself on landing and be read again at the new size. */

import { describe, expect, it } from 'vitest';
import { Grid, type GridItem } from './grid.svelte';

function files(count: number, width: number, height: number): GridItem[] {
	return Array.from(
		{ length: count },
		(_unused, index) => ({ id: `a${index}`, width, height }) as GridItem
	);
}

describe('the size of a page', () => {
	it('stays what the screen allows whatever shape the files on it are', () => {
		const grid = new Grid();
		grid.containerWidth = 6000;
		grid.screenHeight = 900;
		const before = grid.pageRows;

		grid.items = files(400, 9, 16);
		expect(grid.pageRows, 'tall clips resized the page').toBe(before);

		grid.items = files(400, 16, 9);
		expect(grid.pageRows, 'wide clips resized the page').toBe(before);
	});
});
