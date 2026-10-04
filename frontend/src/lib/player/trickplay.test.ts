/*
 * The arithmetic behind the scrub preview.
 *
 * It is here rather than in the component because a wrong answer still looks like a frame from the
 * right video. Nothing on screen says "that is the frame from four seconds earlier".
 */

import { describe, expect, it } from 'vitest';

import { cellOf, frameAt, frameBox, usable, type SpriteSheet } from './trickplay';

function sheet(overrides: Partial<SpriteSheet> = {}): SpriteSheet {
	return { columns: 6, rows: 3, tile_width: 160, frames: 15, ...overrides };
}

const natural = { width: 960, height: 270 }; // 6 x 160 across, 3 x 90 down

describe('which frame', () => {
	it('walks the sheet as the clip runs', () => {
		const strip = sheet();
		expect(frameAt(strip, 0, 30)).toBe(0);
		expect(frameAt(strip, 15, 30)).toBe(7);
		expect(frameAt(strip, 29.9, 30)).toBe(14);
	});

	it('never picks a cell past the last frame', () => {
		// The grid holds 18 cells and only 15 of them have a picture in them. Dragging to the very
		// end has to land on the last frame rather than on one of the three empty ones.
		const strip = sheet();
		expect(frameAt(strip, 30, 30)).toBe(14);
		expect(frameAt(strip, 45, 30)).toBe(14);
		expect(cellOf(strip, 99)).toEqual({ column: 2, row: 2 });
	});

	it('answers the first frame when there is no length to divide by', () => {
		// A clip whose metadata has not arrived yet. Zero rather than a division by zero.
		expect(frameAt(sheet(), 5, 0)).toBe(0);
		expect(frameAt(sheet(), Number.NaN, 30)).toBe(0);
	});

	it('clamps a moment before the start', () => {
		expect(frameAt(sheet(), -5, 30)).toBe(0);
	});
});

describe('where that frame is', () => {
	it('reads the row and column out of the layout', () => {
		const strip = sheet();
		expect(cellOf(strip, 0)).toEqual({ column: 0, row: 0 });
		expect(cellOf(strip, 5)).toEqual({ column: 5, row: 0 });
		expect(cellOf(strip, 6)).toEqual({ column: 0, row: 1 });
		expect(cellOf(strip, 13)).toEqual({ column: 1, row: 2 });
	});

	it('is read from the layout the server sent, not assumed', () => {
		/*
		 * The mutation this file exists for. The same frame, cut out of the same picture, with one
		 * number changed: six across puts frame 7 second on the second row, four across puts it
		 * last on that row, and the window lands on a completely different moment.
		 */
		const asBuilt = frameBox(sheet(), 7, 160, natural);
		const misread = frameBox(sheet({ columns: 4 }), 7, 160, natural);

		expect(asBuilt.backgroundPosition).toBe('-160px -90px');
		expect(misread.backgroundPosition).not.toBe(asBuilt.backgroundPosition);
	});

	it('scales the whole sheet so one tile fills the window', () => {
		// Drawn at half the tile's real width, so the sheet behind it is drawn at half size too.
		const box = frameBox(sheet(), 0, 80, natural);
		expect(box).toMatchObject({
			width: 80,
			height: 45,
			backgroundSize: '480px 135px',
			backgroundPosition: '0px 0px'
		});
	});

	it('takes the tile height from the picture rather than from the video', () => {
		// A portrait clip: the same six columns, a much taller tile, and nothing had to be told.
		const box = frameBox(sheet(), 0, 160, { width: 960, height: 1440 });
		expect(box.height).toBe(480);
	});

	it('falls back to the recorded width when the picture has no size yet', () => {
		const box = frameBox(sheet(), 0, 160, { width: 0, height: 0 });
		expect(box.width).toBe(160);
		expect(box.height).toBe(160);
	});
});

describe('whether there is a strip at all', () => {
	it('accepts a layout that describes a real sheet', () => {
		expect(usable(sheet())).toBe(true);
	});

	it('refuses one that cannot be cut up', () => {
		// Absent, and every shape that would divide by nothing or read past the grid. All of them
		// mean the same thing to the player: no scrub preview, and the scrubber behaves as it did.
		expect(usable(null)).toBe(false);
		expect(usable(undefined)).toBe(false);
		expect(usable(sheet({ columns: 0 }))).toBe(false);
		expect(usable(sheet({ rows: 0 }))).toBe(false);
		expect(usable(sheet({ frames: 0 }))).toBe(false);
		expect(usable(sheet({ frames: 99 }))).toBe(false);
	});
});
