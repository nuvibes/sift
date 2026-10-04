/* Building a wall: which shape a new feed takes it to, and what a removed one leaves.
 *
 * All of it is arithmetic on a grid, which is why none of it needs a browser. Adding is a LADDER
 * (four named shapes, walked in order), so the awkward cases are all on the other side: a cell
 * taken out of the middle, a wall loaded from a saved custom shape. Those are where a wall stops
 * being a grid of rectangles and starts being a picture with a hole in it.
 */

import { describe, expect, it } from 'vitest';

import {
	fill,
	grown,
	layout,
	MOST_CELLS,
	named,
	readShape,
	remove,
	tighten,
	template,
	pictureOf,
	area,
	type Shape
} from './layouts';

/** Every square of the grid, named by which cell covers it, or `.` for nothing. */
function picture(shape: Shape): string[] {
	const rows: string[] = [];
	for (let row = 0; row < shape.rows; row += 1) {
		let line = '';
		for (let col = 0; col < shape.cols; col += 1) {
			const at = shape.slots.findIndex(
				(slot) =>
					row >= slot.row &&
					row < slot.row + slot.rowSpan &&
					col >= slot.col &&
					col < slot.col + slot.colSpan
			);
			line += at === -1 ? '.' : String(at + 1);
		}
		rows.push(line);
	}
	return rows;
}

/* One cell, written here rather than taken from `ONE`.
 *
 * `ONE` is the shape Theater opens on, and that is a product decision that can move. What these
 * tests are about is the arithmetic of growing a wall FROM one cell, so the one
 * cell is theirs. */
const ONE: Shape = { rows: 1, cols: 1, slots: [{ row: 0, col: 0, rowSpan: 1, colSpan: 1 }] };

/* Walls past the top of the ladder, which only a saved custom shape can be. Removing from these is
 * the one case left where a hole is healed rather than a rung picked. */
const SIX: Shape = {
	rows: 2,
	cols: 3,
	slots: [0, 1, 2, 3, 4, 5].map((at) => ({
		row: Math.floor(at / 3),
		col: at % 3,
		rowSpan: 1,
		colSpan: 1
	}))
};
const SEVEN_IN_A_ROW: Shape = {
	rows: 1,
	cols: 7,
	slots: [0, 1, 2, 3, 4, 5, 6].map((col) => ({ row: 0, col, rowSpan: 1, colSpan: 1 }))
};

describe('adding a feed', () => {
	/*
	 * THE LADDER, which is the whole of how a wall gets bigger.
	 *
	 * Not free-form building (a feed putting another beside, above or below itself, a track
	 * pushed in when the space is taken, the hole healed), which works and produces walls nobody
	 * chose, two presses from an L. The shape is a rung, and the cases worth having are which rung,
	 * and what happens at the top of it.
	 */

	it('takes a wall of one to the shape of two', () => {
		expect(picture(grown(ONE) ?? ONE)).toEqual(['12']);
	});

	it('walks every rung, and each one is a shape the picker offers', () => {
		let shape: Shape | null = ONE;
		const walked: string[][] = [];
		while (shape !== null) {
			walked.push(picture(shape));
			expect(named(shape), 'every rung is a preset').not.toBeNull();
			shape = grown(shape);
		}

		expect(walked).toEqual([['1'], ['12'], ['123'], ['12', '34']]);
	});

	it('stops at the top rather than growing a fifth place', () => {
		const top = layout('grid').shape;
		expect(grown(top), 'past the grid a new cell belongs to the strip').toBeNull();
	});

	it('hands out a copy, never the preset itself', () => {
		/* The preset objects are shared by every wall on the screen and by the picker's diagrams.
		   `duplicate` slides slots around in what it is handed, so a shape that WAS the preset would
		   have one wall rewriting the shape all the others read. */
		const first = grown(ONE);
		if (first) {
			first.slots[0].col = 9;
			first.slots.pop();
		}

		expect(grown(ONE)?.slots, 'the ladder handed out the preset itself').toHaveLength(2);
		expect(layout('side_by_side').shape.slots[0].col, 'and a slot inside it').toBe(0);
	});

	it('straightens a shape that is not on the ladder, from how many cells it has', () => {
		/* A wall saved in a custom shape, or one healed by a removal. It is the COUNT
		   that picks the rung, so a stacked pair grown by one is a row of three, not a stack of
		   three, and not a shape with a hole in it. */
		const stacked: Shape = {
			rows: 2,
			cols: 1,
			slots: [
				{ row: 0, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 1, col: 0, rowSpan: 1, colSpan: 1 }
			]
		};

		expect(picture(grown(stacked) ?? stacked)).toEqual(['123']);
	});

	it('has nowhere to go from a wall of more cells than the ladder holds', () => {
		const six = { ...layout('grid').shape, slots: [...layout('grid').shape.slots] };
		six.slots.push({ row: 0, col: 0, rowSpan: 1, colSpan: 1 });

		expect(grown(six)).toBeNull();
	});
});

describe('removing a feed', () => {
	it('keeps the last one', () => {
		expect(remove(ONE, 0).slots.length).toBe(1);
	});

	/*
	 * THE LADDER, WALKED DOWN. The fault this guards: a two-by-two with one cell taken out
	 * staying two rows by two columns with a neighbour stretched over the hole, so three portrait
	 * feeds are drawn two over one. Three is a rung (1x3, the shape adding a third feed makes),
	 * and it is the answer whichever of the four went.
	 */
	it('takes a two-by-two down to three side by side, whichever cell goes', () => {
		for (const index of [0, 1, 2, 3]) {
			const shape = remove(layout('grid').shape, index);
			expect(named(shape), `removing cell ${index + 1} left two over one`).toBe(
				'side_by_side_by_side'
			);
			expect(picture(shape)).toEqual(['123']);
		}
	});

	it('walks every rung back down to one', () => {
		let shape = layout('grid').shape;
		const seen: (string | null)[] = [];
		while (shape.slots.length > 1) {
			shape = remove(shape, 0);
			seen.push(named(shape));
		}
		expect(seen).toEqual(['side_by_side_by_side', 'side_by_side', 'single']);
	});

	it('straightens a saved custom shape the ladder has a rung for', () => {
		/* A stacked three is not a rung. Taken down to two it is the rung for two, which is what
		   adding to a stacked pair does in the other direction (see `grown`). */
		const stacked: Shape = {
			rows: 3,
			cols: 1,
			slots: [
				{ row: 0, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 1, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 2, col: 0, rowSpan: 1, colSpan: 1 }
			]
		};
		expect(named(remove(stacked, 1))).toBe('side_by_side');
	});

	it('hands out a copy of the rung, never the preset itself', () => {
		const shape = remove(layout('grid').shape, 0);
		shape.slots[0].colSpan = 9;
		expect(layout('side_by_side_by_side').shape.slots[0].colSpan).toBe(1);
	});

	it('heals a hole only where no rung names the count', () => {
		/* Six in a two-by-three, one out: five is past the top of the ladder, so the space goes to a
		   neighbour rather than the wall being redrawn as something it never was. */
		const shape = remove(SIX, 5);
		expect(shape.slots).toHaveLength(5);
		expect(picture(shape).join('|')).not.toContain('.');
		expect(picture(shape)).toEqual(['123', '455']);
		expect(named(shape)).toBeNull();
	});

	it('drops a track nothing is left in, where it still heals', () => {
		const shape = remove(SEVEN_IN_A_ROW, 6);
		expect(shape.cols, 'a column of nothing was left behind').toBe(6);
		expect(picture(shape)).toEqual(['123456']);
	});
});

describe('holes', () => {
	it('are filled by growing a cell along a whole edge', () => {
		const holed: Shape = {
			rows: 2,
			cols: 2,
			slots: [
				{ row: 0, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 0, col: 1, rowSpan: 1, colSpan: 1 },
				{ row: 1, col: 0, rowSpan: 1, colSpan: 1 }
			]
		};
		expect(picture(holed)).toEqual(['12', '3.']);
		expect(picture(fill(holed))).toEqual(['12', '33']);
	});

	it('never grows a cell into an L to close one', () => {
		/* One tall cell on the left and a hole beside only its lower half. The tall one cannot take
		 * it (that would make an L), so the cell ABOVE the hole grows down into it instead, and
		 * every cell on the wall is still a rectangle. */
		const holed: Shape = {
			rows: 2,
			cols: 2,
			slots: [
				{ row: 0, col: 0, rowSpan: 2, colSpan: 1 },
				{ row: 0, col: 1, rowSpan: 1, colSpan: 1 }
			]
		};
		const closed = fill(holed);
		expect(picture(closed)).toEqual(['12', '12']);
		expect(closed.slots[0], 'the tall cell was stretched into an L').toEqual({
			row: 0,
			col: 0,
			rowSpan: 2,
			colSpan: 1
		});
	});
});

describe('what is stored', () => {
	it('reads back a shape it wrote', () => {
		/* A healed shape rather than a rung, because it is the one with a span in it: six with the
		   last taken out is five, with the fifth grown across the gap, and `colSpan` is exactly what
		   the two spellings of a stored shape disagree about. */
		const shape = remove(SIX, 5);
		expect(shape.slots[4].colSpan, 'this test needs a span to be about anything').toBe(2);
		expect(readShape(JSON.parse(JSON.stringify(shape)))).toEqual(shape);
	});

	it("reads the server's own spelling of a span", () => {
		const read = readShape({
			rows: 1,
			cols: 2,
			slots: [{ row: 0, col: 0, row_span: 1, col_span: 2 }]
		});
		expect(read?.slots[0].colSpan).toBe(2);
	});

	it('refuses a shape whose cells overlap', () => {
		expect(
			readShape({
				rows: 1,
				cols: 2,
				slots: [
					{ row: 0, col: 0, rowSpan: 1, colSpan: 2 },
					{ row: 0, col: 1, rowSpan: 1, colSpan: 1 }
				]
			}),
			'two cells over one square would draw one on top of the other'
		).toBeNull();
	});

	it('refuses a cell that reaches past the edge', () => {
		expect(
			readShape({ rows: 1, cols: 1, slots: [{ row: 0, col: 0, rowSpan: 1, colSpan: 2 }] })
		).toBeNull();
	});

	it('refuses more cells than a wall may hold', () => {
		const slots = Array.from({ length: MOST_CELLS + 1 }, (_, at) => ({
			row: 0,
			col: at,
			rowSpan: 1,
			colSpan: 1
		}));
		expect(readShape({ rows: 1, cols: MOST_CELLS + 1, slots })).toBeNull();
	});

	it('names a shape that is one of the presets, and nothing else', () => {
		expect(named(layout('grid').shape)).toBe('grid');
		expect(named(remove(SIX, 0))).toBeNull();
	});
});

describe('what CSS is handed', () => {
	it('names as many tracks as the shape has', () => {
		expect(template({ rows: 2, cols: 3, slots: [] })).toBe(
			'repeat(2, var(--feed-height, 1fr)) / repeat(3, minmax(0, auto))'
		);
	});

	/*
	 * THE ROWS ARE THE FEED HEIGHT, WITH `1fr` ONLY AS THE FALLBACK.
	 *
	 * Asserted as its own test rather than left inside the string above, because the string is the
	 * kind of thing somebody re-copies from a failure message without reading it, and this is the
	 * one part of it that carries a decision. Rows of `1fr` fill the wall whatever height the feeds
	 * came out at, so a wall that has to come down to fit across would leave a band of ground above
	 * and below EVERY row: black between the two rows of a two-by-two, and the outside edges never
	 * reached. See `template`, and `align-content: center` on the wall, which can do nothing at
	 * all while the tracks already fill the box.
	 */
	it('sizes the rows from the feed height, falling back to a share of the wall', () => {
		const rows = template({ rows: 2, cols: 2, slots: [] }).split(' / ')[0];
		expect(rows).toContain('var(--feed-height');
		expect(rows, 'an unmeasured wall still lays out').toContain('1fr');
	});

	it('counts tracks from one, because CSS does', () => {
		expect(area({ row: 0, col: 0, rowSpan: 1, colSpan: 2 })).toBe('1 / 1 / span 1 / span 2');
	});
});

describe('a PICTURE of a shape, which is not the shape itself', () => {
	/*
	 * `pictureOf` exists because reusing `template` for a diagram draws both places on top of each
	 * other. That is the failure to hold: `auto` columns are sized by what is in them, and a
	 * diagram has nothing in it, so a two-column picker measures `0px 0px` and reads as a layout
	 * fault rather than as a wrong number.
	 */

	it('gives every place the same square, in both directions', () => {
		expect(pictureOf({ rows: 2, cols: 3, slots: [] }, '10px')).toBe(
			'repeat(2, 10px) / repeat(3, 10px)'
		);
	});

	it('never hands a diagram a track sized by its contents', () => {
		// The whole of the difference, asserted as the property rather than as a string: a picture
		// has nothing in its tracks, so anything content-sized resolves to nothing at all.
		const drawn = pictureOf({ rows: 2, cols: 2, slots: [] }, '12px');
		expect(drawn).not.toContain('auto');
		expect(drawn).not.toContain('fr');
		expect(drawn).not.toContain('--feed-height');
	});

	it("is a different answer from the wall's own template for the same shape", () => {
		// If these ever come out equal, one of the two has been changed to the other's job and the
		// diagram is back to being drawn by a rule written for a wall of pictures.
		const shape: Shape = { rows: 2, cols: 2, slots: [] };
		expect(pictureOf(shape, '10px')).not.toBe(template(shape));
	});

	it('says where the places are, at whatever size it is given', () => {
		// The side is the caller's, so a picker can draw the same diagram large in a menu and small
		// on a bar. Nothing about the shape may come from the size.
		const small = pictureOf({ rows: 3, cols: 1, slots: [] }, '6px');
		const large = pictureOf({ rows: 3, cols: 1, slots: [] }, '24px');
		expect(small.replace(/6px/g, 'SIDE')).toBe(large.replace(/24px/g, 'SIDE'));
	});
});

describe('tightening', () => {
	it('leaves a full wall alone', () => {
		const shape = layout('grid').shape;
		expect(tighten(shape)).toEqual(shape);
	});

	it('drops a ROW nothing sits in, and brings what is under it up', () => {
		/* The column half of this is covered by removing a feed from a wall that is one row of
		   three. The ROW half needs its own case: every preset is one row, or is square, so no
		   other test reaches a wall with an empty row in the middle of it, and the arithmetic
		   could be made to do nothing at all without one of them noticing.

		   A stripe of empty wall is not a subtle failure. It is a band of ground across the middle
		   of what is meant to be feeds against each other. */
		const gap: Shape = {
			rows: 3,
			cols: 1,
			slots: [
				{ row: 0, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 2, col: 0, rowSpan: 1, colSpan: 1 }
			]
		};

		const tightened = tighten(gap);

		expect(tightened.rows).toBe(2);
		expect(picture(tightened)).toEqual(['1', '2']);
	});
});
