import { describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Cell } from './cell.svelte';
import { ASSUMED, feedHeight, wallAspect } from './fit';
import { layout, type Shape } from './layouts';

/* The numbers below are the real ones: a 16:9 window, the shapes the picker offers, and the two
   aspect ratios a library of clips is actually made of. A rule checked against round numbers chosen
   to sit either side of a threshold is a rule nobody has checked against what it runs on. */

const WINDOW = { width: 1600, height: 800 };
const PORTRAIT = 1080 / 1920;
const LANDSCAPE = 1920 / 1080;
const GAP = 8;

const shapeOf = (id: string): Shape => layout(id).shape;

describe('how tall a row of feeds is', () => {
	it('gives the whole height to portrait feeds, which fit across easily', () => {
		/* Two 9:16 clips at 800 tall are 450 each: 908 with the gap, against 1600 of window. Nothing
		   has to give, so the row is the full height and the spare width is at the outside edges. */
		const tall = feedHeight(shapeOf('side_by_side'), WINDOW, [PORTRAIT, PORTRAIT], GAP);
		expect(tall).toBe(800);
	});

	it('brings the row down when the feeds would be wider than the wall', () => {
		/* The case CSS cannot do on its own, and the reason this is arithmetic. Two 16:9 clips at 800
		   tall are 1422 each (2852 against 1600 of window), so the height comes down by exactly
		   the overflow and the feeds keep their shape instead of being squashed into the width. */
		const tall = feedHeight(shapeOf('side_by_side'), WINDOW, [LANDSCAPE, LANDSCAPE], GAP);
		expect(tall).not.toBeNull();
		expect(tall!).toBeLessThan(800);
		// And at that height they fit exactly: two of them, plus the gap, is the window.
		expect(tall! * LANDSCAPE * 2 + GAP).toBeCloseTo(WINDOW.width, 0);
	});

	it('splits the height between rows, and the gaps between them', () => {
		const tall = feedHeight(shapeOf('grid'), WINDOW, [PORTRAIT, PORTRAIT, PORTRAIT, PORTRAIT], GAP);
		expect(tall).toBe((800 - GAP) / 2);
	});

	it('sizes a column by the WIDEST thing in it, so the columns stay straight', () => {
		/* A landscape clip above a portrait one. If the column took the narrower of the two, the feed
		   above it would hang out past the column and over its neighbour.

		   In a NARROW window, because that is the only place the difference shows: with room to
		   spare every arrangement of these gets the full height and the two answers agree. */
		const narrow = { width: 700, height: 800 };
		const mixed = feedHeight(
			shapeOf('grid'),
			narrow,
			[LANDSCAPE, PORTRAIT, PORTRAIT, PORTRAIT],
			GAP
		);
		const allPortrait = feedHeight(
			shapeOf('grid'),
			narrow,
			[PORTRAIT, PORTRAIT, PORTRAIT, PORTRAIT],
			GAP
		);
		expect(mixed).not.toBeNull();
		expect(mixed!).toBeLessThan(allPortrait!);
	});

	/*
	 * WHAT IS LEFT AT THE SIDES IS THE DESIGN.
	 *
	 * The alternative is a justified wall: each ROW scaled out until it exactly spans the width,
	 * the way `$lib/grid/justify` lays a page of tiles out. That fills the screen with nothing
	 * cropped, and it gives up the one thing this wall is for: columns that line up under each
	 * other. The columns are kept.
	 *
	 * So this pins the half nobody would think to pin: with room to spare the feeds do NOT grow
	 * into it. A justified wall would answer a height raised until the row spanned 2400; this one
	 * answers the whole height and comes up 570-odd pixels short, which is the ground at the two
	 * edges.
	 */
	it('leaves what it cannot use at the SIDES rather than stretching into it', () => {
		const roomy = { width: 2400, height: 800 };
		const four = [PORTRAIT, PORTRAIT, PORTRAIT, PORTRAIT];
		const tall = feedHeight(shapeOf('grid'), roomy, four, GAP);
		expect(tall, 'the wall grew its feeds to reach the outside edges').toBe((800 - GAP) / 2);
		const across = tall! * PORTRAIT * 2 + GAP;
		expect(across).toBeLessThan(roomy.width);
	});

	it('assumes a shape for a feed that has not said, rather than collapsing it', () => {
		/* Every cell passes through this on the way to knowing, and a wall that collapsed for the
		   moment before is a wall that jumps on arrival. */
		const guessed = feedHeight(shapeOf('side_by_side'), WINDOW, [null, null], GAP);
		const said = feedHeight(shapeOf('side_by_side'), WINDOW, [ASSUMED, ASSUMED], GAP);
		expect(guessed).toBe(said);
	});

	it('says nothing at all until the wall has been measured', () => {
		/* Zero would collapse every feed on the screen. The caller leaves the grid alone instead. */
		expect(feedHeight(shapeOf('grid'), { width: 0, height: 800 }, [PORTRAIT], GAP)).toBeNull();
		expect(feedHeight(shapeOf('grid'), { width: 1600, height: 0 }, [PORTRAIT], GAP)).toBeNull();
	});

	it('keeps a spanning cell from pushing out every column it covers', () => {
		/* Only the retired layouts had spans, and a wall saved under one still opens, so the sum has
		   to be right for it. A cell two tracks wide divides its width across the two; taking its full
		   width for each would make the wall twice as wide as it is and halve the height for nothing. */
		const wide: Shape = {
			rows: 2,
			cols: 2,
			slots: [
				{ row: 0, col: 0, rowSpan: 1, colSpan: 2 },
				{ row: 1, col: 0, rowSpan: 1, colSpan: 1 },
				{ row: 1, col: 1, rowSpan: 1, colSpan: 1 }
			]
		};
		/* Asserted against the closed form rather than against another call, because "smaller than
		   the other one" is true of the wrong answer as well: a spanning cell that claimed its whole
		   width in EVERY column it covers is also smaller, by twice as much. */
		const narrow = { width: 700, height: 800 };
		const rowTall = (narrow.height - GAP) / 2;
		// The wide cell is `rowTall * LANDSCAPE` across, spread over two columns and the gap between
		// them; the pair below it is two portraits. The widest column decides, doubled, plus the gap.
		const spread = (rowTall * LANDSCAPE - GAP) / 2;
		const column = Math.max(spread, rowTall * PORTRAIT);
		const wanted = rowTall * ((narrow.width - GAP) / (column * 2));

		expect(feedHeight(wide, narrow, [LANDSCAPE, PORTRAIT, PORTRAIT], GAP)).toBeCloseTo(wanted, 4);
	});
});

describe('a wall laid out from real cells, with a shape chosen on one', () => {
	/* The gap this closes: every case above hands `feedHeight` a list of numbers, which is the
	   arithmetic and not the path. What the wall actually passes is `cells.map((one) => one.shape)`,
	   and a cell's shape comes from the CHOICE when there is one and from the file otherwise. A
	   chosen shape that never reached the layout would draw a letterboxed cell in a track sized for
	   the file: ground down one side, and nothing in the numbers above can see it. */

	async function playing(what: { width: number; height: number }) {
		const cell = new Cell();
		vi.spyOn(api, 'get').mockResolvedValue({
			items: [{ id: 'a', media_type: 'video', duration_ms: 1000, thumb: true, ...what }],
			total: 1
		});
		vi.spyOn(api, 'post').mockResolvedValue({
			route: 'direct',
			streamable: true,
			url: '/stream',
			reason: '',
			scale_height: null,
			resume_ms: null
		});
		await cell.restart();
		return cell;
	}

	it('sizes the row from the shape a cell was HELD to, not the shape of its file', async () => {
		const landscape = { width: 1920, height: 1080 };
		const two = [await playing(landscape), await playing(landscape)];
		const asFilmed = feedHeight(shapeOf('side_by_side'), WINDOW, [two[0].shape, two[1].shape], GAP);

		// One of them is pinned to portrait. It is the narrower thing in its column, so the pair
		// does not have to come down as far to fit across the wall.
		two[1].aspect = 'tall';
		const held = feedHeight(shapeOf('side_by_side'), WINDOW, [two[0].shape, two[1].shape], GAP);

		expect(asFilmed).not.toBeNull();
		expect(held).not.toBeNull();
		expect(
			held!,
			'the chosen shape never reached the layout, so the wall is still sized for the file'
		).toBeGreaterThan(asFilmed!);
		// And it is the arithmetic for the shape that was asked for, exactly: one landscape beside
		// one portrait, at that height, is the whole window less the gap.
		expect(held! * LANDSCAPE + held! * (9 / 16) + GAP).toBeCloseTo(WINDOW.width, 0);
	});

	it('assumes the commonest shape for a Dynamic cell with nothing playing yet', () => {
		// `Cell.shape` answers null there deliberately, so the layout has to be the thing that
		// decides. A wall of empty cells laid out as though they were zero wide is a collapsed wall.
		const empty = new Cell();
		expect(empty.shape).toBeNull();
		expect(feedHeight(shapeOf('side_by_side'), WINDOW, [empty.shape, empty.shape], GAP)).toBe(
			feedHeight(shapeOf('side_by_side'), WINDOW, [ASSUMED, ASSUMED], GAP)
		);
	});
});

describe('the shape the whole wall comes out as', () => {
	/* `wallAspect` is what the corner panel fits a wall to. It is the same reasoning as
	   `feedHeight` with the height fixed at one, so the two can disagree only by one of them
	   being wrong, which is exactly the kind of wrong that draws correctly at one size and not
	   at another. */

	it('answers nothing while no cell has said what it is playing', () => {
		// Null is the caller's cue to fit each side on its own. A number here would be a wall
		// fitted to a guess.
		expect(wallAspect(shapeOf('grid'), [null, null, null, null])).toBeNull();
	});

	it('is twice as wide as one feed when two sit side by side', () => {
		expect(wallAspect(shapeOf('side_by_side'), [LANDSCAPE, LANDSCAPE])).toBeCloseTo(
			LANDSCAPE * 2,
			6
		);
	});

	it('is the same as one feed when four of them make a square', () => {
		// Two across and two down: twice as wide and twice as tall, so the proportions are the
		// feed's own. A wall that answered 2x here would be fitted into half the height it needs.
		expect(wallAspect(shapeOf('grid'), [PORTRAIT, PORTRAIT, PORTRAIT, PORTRAIT])).toBeCloseTo(
			PORTRAIT,
			6
		);
	});

	it('takes the WIDEST feed in each column, as the wall itself does', () => {
		const mixed = wallAspect(shapeOf('grid'), [LANDSCAPE, PORTRAIT, PORTRAIT, PORTRAIT]);
		const narrow = wallAspect(shapeOf('grid'), [PORTRAIT, PORTRAIT, PORTRAIT, PORTRAIT]);
		expect(mixed).not.toBeNull();
		expect(mixed!, 'a landscape feed did not widen the column it is in').toBeGreaterThan(narrow!);
		// The first column is the landscape one, the second is portrait; both rows deep.
		expect(mixed!).toBeCloseTo((LANDSCAPE + PORTRAIT) / 2, 6);
	});

	it('assumes the commonest shape for a cell that has not said, rather than dropping it', () => {
		// One answered and one not is the case a real wall spends its first moment in. Treating the
		// silent one as nothing would make the wall half as wide as it is about to be.
		expect(wallAspect(shapeOf('side_by_side'), [LANDSCAPE, null])).toBeCloseTo(
			LANDSCAPE + ASSUMED,
			6
		);
	});
});
