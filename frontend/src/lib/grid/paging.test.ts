/* Paging by whole ROWS, so every page is one size and the pager does not slide. */

import { describe, expect, test } from 'vitest';

import {
	averageAspect,
	fillRows,
	itemsToFill,
	justify,
	rowsThatFit,
	GRID_GUTTER,
	SIZE_STEPS,
	snapRowHeight,
	UNKNOWN_ASPECT,
	type Layable
} from './justify';

function item(id: string, width: number | null, height: number | null): Layable {
	return { id, width, height };
}

function run(count: number, width: number, height: number, from = 0): Layable[] {
	return Array.from({ length: count }, (_, index) => item(`i${from + index}`, width, height));
}

/* The application's own gap, so these assertions are about the grid Sift draws. */
const GUTTER = GRID_GUTTER;

describe('how many rows fit', () => {
	test('counts the gaps between rows, not one per row', () => {
		expect(rowsThatFit(616, 200, GUTTER)).toBe(3);
		expect(rowsThatFit(823, 200, GUTTER)).toBe(3);
		expect(rowsThatFit(824, 200, GUTTER)).toBe(4);
	});

	test('a row that would be cut off is not counted', () => {
		// One pixel short of four rows is three: half a row reads as "scroll for more".
		expect(rowsThatFit(823, 200, GUTTER)).toBe(3);
	});

	test('never zero, however short the window', () => {
		expect(rowsThatFit(10, 200, GUTTER)).toBe(1);
		expect(rowsThatFit(0, 200, GUTTER)).toBe(1);
		expect(rowsThatFit(-100, 200, GUTTER)).toBe(1);
	});
});

describe('snapping the row height', () => {
	test('rows of the snapped height fill the space exactly, WHEN it snaps at all', () => {
		/* Snapping adjusts, never overrules: the exact fill is asserted only where it moved. */
		let snapped = 0;
		for (const available of [640, 700, 800, 900, 1080, 1440]) {
			for (const target of SIZE_STEPS) {
				const height = snapRowHeight(target, available, GUTTER);
				if (height === target) continue;
				snapped += 1;
				const rows = rowsThatFit(available, height, GUTTER);
				const used = rows * height + GUTTER * (rows - 1);
				expect(Math.abs(available - used)).toBeLessThanOrEqual(rows);
			}
		}
		// The positive control: a snap that never fired would satisfy every assertion above.
		expect(snapped, 'nothing snapped at all, so nothing was checked').toBeGreaterThan(0);
	});

	test('it takes the nearer of the two row counts, not simply the one that fits', () => {
		// Four rows of 194 is closer to 220 than three stretched to 261.
		const height = snapRowHeight(220, 800, GUTTER);
		expect(height).toBe(194);
		expect(rowsThatFit(800, height, GUTTER)).toBe(4);
	});

	test('every notch on the ladder draws a DIFFERENT size, on every window worth using', () => {
		/*
		 * The top two notches must never draw identical tiles at any height: `SNAP_LIMIT` keeps
		 * each in its own band.
		 */
		for (let available = 200; available <= 1400; available++) {
			const heights = SIZE_STEPS.map((target) => snapRowHeight(target, available, GRID_GUTTER));
			for (let i = 1; i < heights.length; i++) {
				expect(
					heights[i],
					`at ${available}px of grid, notches ${i} and ${i + 1} both draw ${heights[i]}px`
				).toBeGreaterThan(heights[i - 1]);
			}
		}
	});

	test('it never snaps below the size a thumbnail stops being recognisable at', () => {
		// Not a wall of stamps.
		expect(snapRowHeight(120, 200, GUTTER)).toBeGreaterThanOrEqual(80);
	});

	test('it stays near what was asked for', () => {
		for (const available of [700, 820, 950, 1100, 1300]) {
			for (const target of SIZE_STEPS) {
				const height = snapRowHeight(target, available, GUTTER);
				expect(Math.abs(height - target) / target).toBeLessThan(0.25);
			}
		}
	});
});

describe('filling a page with whole rows', () => {
	const options = { containerWidth: 1000, targetHeight: 200, gutter: GUTTER };

	test('it keeps exactly the rows asked for and says how many files that took', () => {
		const items = run(40, 1600, 900);
		const filled = fillRows(items, { ...options, rows: 3 });

		expect(filled.rows).toHaveLength(3);
		expect(filled.full).toBe(true);
		expect(filled.used).toBe(filled.rows.reduce((n, row) => n + row.tiles.length, 0));
		expect(filled.used).toBeLessThan(items.length);
	});

	test('the surplus is discarded, and `used` is what the next page starts after', () => {
		// Everything fetched is not everything shown, or the rest would never be seen.
		const items = run(40, 1600, 900);
		const first = fillRows(items, { ...options, rows: 2 });
		const rest = fillRows(items.slice(first.used), { ...options, rows: 2 });

		const shown = [
			...first.rows.flatMap((row) => row.tiles.map((tile) => tile.id)),
			...rest.rows.flatMap((row) => row.tiles.map((tile) => tile.id))
		];
		expect(new Set(shown).size).toBe(shown.length);
		expect(shown).toEqual(items.slice(0, shown.length).map((each) => each.id));
	});

	test('every row it keeps is a full one', () => {
		const filled = fillRows(run(40, 1600, 900), { ...options, rows: 3 });
		expect(filled.rows.every((row) => row.filled)).toBe(true);
	});

	test('too few files is not full, which is how the caller knows to fetch more', () => {
		const filled = fillRows(run(4, 1600, 900), { ...options, rows: 3 });

		expect(filled.full).toBe(false);
		expect(filled.used).toBe(4);
	});

	test('an exact fit with nothing left over is still full', () => {
		// A page filled to the pixel by the last file is full.
		const items = run(40, 1600, 900);
		const three = fillRows(items, { ...options, rows: 3 });
		const exact = fillRows(items.slice(0, three.used), { ...options, rows: 3 });

		expect(exact.full).toBe(true);
		expect(exact.used).toBe(three.used);
	});

	test('mixed proportions change how many files a page holds, not how many rows', () => {
		// Portrait clips pack more into a row; a page counted in files would vary in height.
		const wide = fillRows(run(200, 1920, 1080), { ...options, rows: 4 });
		const tall = fillRows(run(200, 1080, 1920), { ...options, rows: 4 });

		expect(wide.rows).toHaveLength(4);
		expect(tall.rows).toHaveLength(4);
		expect(tall.used).toBeGreaterThan(wide.used * 2);
	});

	test('and the page height barely moves, though it is not identical to the pixel', () => {
		/* Rows differ in height by a couple of percent, not by whole rows. */
		const height = (filled: ReturnType<typeof fillRows>) =>
			filled.rows.reduce((total, row) => total + row.height, 0) + GUTTER * (filled.rows.length - 1);

		const wide = height(fillRows(run(200, 1920, 1080), { ...options, rows: 4 }));
		const tall = height(fillRows(run(200, 1080, 1920), { ...options, rows: 4 }));

		expect(Math.abs(wide - tall) / Math.max(wide, tall)).toBeLessThan(0.05);
	});

	test('an empty library is one empty page, not a crash', () => {
		const filled = fillRows([], { ...options, rows: 3 });
		expect(filled.rows).toEqual([]);
		expect(filled.used).toBe(0);
		expect(filled.full).toBe(false);
	});
});

describe('how many files to ask for', () => {
	test('enough that an ordinary page is filled by the first request', () => {
		const geometry = { containerWidth: 1600, rowHeight: 200 };
		for (const rows of [2, 4, 6, 8]) {
			const asked = itemsToFill(rows, geometry);
			const filled = fillRows(run(asked, 1600, 900), {
				containerWidth: geometry.containerWidth,
				targetHeight: geometry.rowHeight,
				gutter: GUTTER,
				rows
			});
			expect(filled.full).toBe(true);
		}
	});

	test('it reads the shapes on screen rather than assuming them', () => {
		const geometry = { containerWidth: 1600, rowHeight: 200 };
		const forTall = itemsToFill(4, { ...geometry, averageAspect: 9 / 16 });
		const forWide = itemsToFill(4, { ...geometry, averageAspect: 16 / 9 });

		expect(forTall).toBeGreaterThan(forWide * 2);
	});

	test('it never asks for fewer files than there are rows', () => {
		expect(itemsToFill(6, { containerWidth: 0, rowHeight: 200 })).toBeGreaterThanOrEqual(6);
		expect(itemsToFill(6, { containerWidth: 1600, rowHeight: 0 })).toBeGreaterThanOrEqual(6);
	});

	test('a nonsense average does not produce a nonsense request', () => {
		// A library mid-import can hand this something extreme.
		const geometry = { containerWidth: 1600, rowHeight: 200 };
		expect(itemsToFill(4, { ...geometry, averageAspect: 0 })).toBeLessThan(1000);
		expect(itemsToFill(4, { ...geometry, averageAspect: 0.001 })).toBeLessThan(1000);
		expect(itemsToFill(4, { ...geometry, averageAspect: 500 })).toBeGreaterThanOrEqual(4);
	});
});

describe('the average shape on screen', () => {
	test('nothing on screen is the placeholder shape', () => {
		expect(averageAspect([])).toBe(UNKNOWN_ASPECT);
	});

	test('a file not yet measured counts as the placeholder rather than as nothing', () => {
		expect(averageAspect([item('unmeasured', null, null)])).toBe(UNKNOWN_ASPECT);
	});

	test('it is the mean of what is there', () => {
		expect(averageAspect([item('a', 2000, 1000), item('b', 1000, 1000)])).toBe(1.5);
	});
});

describe('what a filled row promises', () => {
	test('a full row still spans the container to the pixel', () => {
		const filled = fillRows(run(40, 1600, 900), {
			containerWidth: 1000,
			targetHeight: 200,
			gutter: GUTTER,
			rows: 3
		});
		for (const row of filled.rows) {
			const used =
				row.tiles.reduce((total, tile) => total + tile.width, 0) + GUTTER * (row.tiles.length - 1);
			expect(used).toBe(1000);
		}
	});

	test('only the leftover row is ever marked unfilled', () => {
		const rows = justify(run(40, 1600, 900), {
			containerWidth: 1000,
			targetHeight: 200,
			gutter: GUTTER
		});
		expect(rows.slice(0, -1).every((row) => row.filled)).toBe(true);
		expect(rows[rows.length - 1].filled).toBe(false);
	});
});
