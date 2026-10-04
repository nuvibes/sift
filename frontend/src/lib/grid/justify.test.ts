/*
 * The layout arithmetic.
 *
 * Worth testing here rather than in a browser for a reason that is not just convenience: the test
 * environment has no layout engine at all, so a browser-based test of this would be measuring
 * nothing. These are pure numbers in and pure numbers out, which is the whole reason the algorithm
 * was written as a function rather than as something the component works out while rendering.
 */

import { describe, expect, test } from 'vitest';
import {
	aspectOf,
	CAPTION_HEIGHT,
	heightForWidth,
	justify,
	rowsThatFit,
	snapRowHeight,
	targetRowHeight,
	UNKNOWN_ASPECT,
	wallOf,
	type Layable
} from './justify';

function item(id: string, width: number | null, height: number | null): Layable {
	return { id, width, height };
}

const OPTIONS = { containerWidth: 1000, targetHeight: 200, gutter: 12 };

describe('rows are justified', () => {
	test('a full row spans the container exactly', () => {
		const items = Array.from({ length: 12 }, (_, index) => item(`a${index}`, 1600, 900));
		const rows = justify(items, OPTIONS);

		// Every row but the last is complete, and a complete row fills the width to the pixel. A
		// row that is a few pixels short reads as a wobbling right-hand margin down the page.
		for (const row of rows.slice(0, -1)) {
			const used =
				row.tiles.reduce((total, tile) => total + tile.width, 0) +
				OPTIONS.gutter * (row.tiles.length - 1);
			expect(used).toBe(OPTIONS.containerWidth);
		}
	});

	test('every tile in a row is the same height', () => {
		const items = [
			item('wide', 1920, 1080),
			item('tall', 1080, 1920),
			item('square', 800, 800),
			item('wider', 2560, 1080)
		];
		const rows = justify(items, OPTIONS);

		for (const row of rows) {
			for (const tile of row.tiles) expect(tile.height).toBe(row.height);
		}
	});

	test('proportions survive the layout, which is the point of it', () => {
		// A vertical phone video and a wide screenshot, side by side. Neither is cropped and
		// neither is padded: the widths differ instead.
		const rows = justify([item('portrait', 1080, 1920), item('landscape', 1920, 1080)], {
			...OPTIONS,
			containerWidth: 2000
		});

		const [portrait, landscape] = rows[0].tiles;
		expect(portrait.width / portrait.height).toBeCloseTo(1080 / 1920, 1);
		expect(landscape.width / landscape.height).toBeCloseTo(1920 / 1080, 1);
		expect(portrait.width).toBeLessThan(landscape.width);
	});

	test('the last row keeps its natural size instead of being stretched across the screen', () => {
		// Thirteen identical items: twelve fill rows, one is left over. Stretching it would blow a
		// single thumbnail up to the full width of the window, three times the size of everything
		// above it, for no reason except that it was last.
		const items = Array.from({ length: 13 }, (_, index) => item(`a${index}`, 1600, 900));
		const rows = justify(items, OPTIONS);
		const last = rows[rows.length - 1];

		expect(last.tiles).toHaveLength(1);
		expect(last.height).toBe(OPTIONS.targetHeight);
		expect(last.tiles[0].width).toBeLessThan(OPTIONS.containerWidth);
	});

	test('a very wide item alone on the last row is scaled down rather than overflowing', () => {
		const rows = justify([item('panorama', 6000, 400)], OPTIONS);
		const used = rows[0].tiles[0].width;

		expect(used).toBeLessThanOrEqual(OPTIONS.containerWidth);
	});

	test('every item is placed exactly once', () => {
		const items = Array.from({ length: 37 }, (_, index) => item(`a${index}`, 1600, 900));
		const placed = justify(items, OPTIONS).flatMap((row) => row.tiles.map((tile) => tile.id));

		expect(placed).toHaveLength(37);
		expect(new Set(placed).size).toBe(37);
	});

	test('nothing to lay out is no rows, not a crash', () => {
		expect(justify([], OPTIONS)).toEqual([]);
		expect(justify([item('a', 100, 100)], { ...OPTIONS, containerWidth: 0 })).toEqual([]);
	});
});

describe('an item nobody has measured yet', () => {
	test('a file still being imported gets a placeholder shape rather than a divide by zero', () => {
		expect(aspectOf(item('new', null, null))).toBe(UNKNOWN_ASPECT);
		expect(aspectOf(item('half', 100, null))).toBe(UNKNOWN_ASPECT);
		expect(aspectOf(item('zero', 100, 0))).toBe(UNKNOWN_ASPECT);
	});

	test('an absurd shape is clamped so it cannot squash a whole row', () => {
		// One broken file reporting 10000x1 would otherwise take a row to itself and reduce it to a
		// couple of pixels tall.
		const aspect = aspectOf(item('broken', 10_000, 1));
		expect(aspect).toBeLessThanOrEqual(5);

		const rows = justify([item('broken', 10_000, 1), item('normal', 1600, 900)], OPTIONS);
		expect(rows[0].height).toBeGreaterThan(20);
	});

	test('an unmeasured item still lays out beside measured ones', () => {
		const rows = justify([item('known', 1600, 900), item('unknown', null, null)], {
			...OPTIONS,
			containerWidth: 700
		});
		expect(rows[0].tiles).toHaveLength(2);
		expect(rows[0].tiles[1].width).toBeGreaterThan(0);
	});
});

describe('row height follows the window', () => {
	test('a bigger window gets taller rows, not more of them', () => {
		expect(heightForWidth(1600)).toBeGreaterThan(heightForWidth(800));
		expect(heightForWidth(360)).toBe(120);
	});
});

/*
 * No virtual window: a page is a couple of screenfuls (a bounded, small number of tiles), so
 * every row of it is in the document and the browser is left to scroll them. Paging by whole rows
 * is tested in `paging.test.ts`.
 */

describe('how tall a row of tiles should be', () => {
	/*
	 * The rule the size slider MEANS, in one place.
	 *
	 * A constant row height on one screen would make the notch move Browse and do nothing there,
	 * and that screen's rows the same height on a phone as on a 32-inch monitor, because the
	 * window's own answer would not be asked for either. Two screens, one question, one answer.
	 */
	test('is the notch somebody chose, wherever they chose it', () => {
		expect(targetRowHeight(260, 1600)).toBe(260);
		// ...and the window has no say once a notch is chosen, which is the whole point of choosing.
		expect(targetRowHeight(260, 360)).toBe(260);
	});

	test('is the window own answer while nobody has chosen', () => {
		expect(targetRowHeight(null, 1600)).toBe(heightForWidth(1600));
		expect(targetRowHeight(null, 360)).toBe(heightForWidth(360));
		// Not a constant: the two ends of the ladder have to differ, or "responsive" means nothing.
		expect(targetRowHeight(null, 1600)).not.toBe(targetRowHeight(null, 360));
	});
});

describe('a captioned wall keeps a line under every row', () => {
	const two = [
		{ height: 100, filled: true, tiles: [{ id: 'a', width: 200, height: 100 }] },
		{ height: 90, filled: true, tiles: [{ id: 'b', width: 180, height: 90 }] }
	];

	test('places each row below the one above, its line included', () => {
		const wall = wallOf(two, 8, CAPTION_HEIGHT);
		expect(wall.tiles[1].y).toBe(100 + CAPTION_HEIGHT + 8);
		// The last row's line is inside the wall, so nothing below it is drawn over it.
		expect(wall.height).toBe(100 + 90 + 2 * CAPTION_HEIGHT + 8);
	});

	test('leaves a wall of files exactly as it was', () => {
		expect(wallOf(two, 8).tiles[1].y).toBe(108);
		expect(wallOf(two, 8).height).toBe(198);
	});

	test('divides a screen into whole rows with their lines, and answers the tiles alone', () => {
		const tiles = snapRowHeight(180, 800, 8, CAPTION_HEIGHT);
		const rows = rowsThatFit(800, tiles + CAPTION_HEIGHT, 8);
		// Whole rows, lines and gutters fill the space with less than a row's worth left over.
		const used = rows * (tiles + CAPTION_HEIGHT) + (rows - 1) * 8;
		expect(used).toBeLessThanOrEqual(800);
		expect(800 - used).toBeLessThan(rows + 1);
		expect(tiles).toBeLessThan(snapRowHeight(180, 800, 8));
	});
});
