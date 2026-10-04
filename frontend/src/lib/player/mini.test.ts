/* Keeping the mini player on the screen.
 *
 * The arithmetic is the whole of what can go wrong here: a panel remembered in the corner of a wide
 * monitor is off the edge of a laptop, and a panel nobody can see is a video nobody can stop.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
	fit,
	handover,
	MIN_HEIGHT,
	MIN_WIDTH,
	mini,
	resized,
	restingPlace,
	shapedTo
} from './mini.svelte';

const WINDOW = { width: 1280, height: 800 };

beforeEach(() => {
	localStorage.clear();
	mini.close();
});

describe('where it sits', () => {
	it('rests out of the way in the corner when it has never been moved', () => {
		const place = restingPlace(WINDOW);

		expect(place.x + place.width).toBe(WINDOW.width - 16);
		expect(place.y + place.height).toBe(WINDOW.height - 16);
	});

	it('opens at the size that was asked for on a window with room for it', () => {
		// About 520 wide in the corner of a 2560-wide window, a `--space-4` off each edge. The
		// stylesheet holds the number; with no document to read it from, the module's own floor is
		// the same one.
		const place = restingPlace({ width: 2560, height: 1400 });

		expect(place.width).toBe(520);
		expect(place.x + place.width).toBe(2560 - 16);
		expect(place.y + place.height).toBe(1400 - 16);
	});

	it('takes a share of a window too narrow for that, rather than a third of the screen', () => {
		// 520 on a 1280-wide laptop is a panel that is in the way rather than out of it.
		const place = restingPlace(WINDOW);

		expect(place.width).toBe(Math.round(WINDOW.width * 0.26));
		expect(place.width).toBeLessThan(520);
	});

	it('takes its height from the shape of what is playing, and still lands in the corner', () => {
		/* A tall clip. Computing the height from a fixed shape and reshaping a frame later would
		   bring the panel to rest a few pixels off the bottom on anything that was not 16:9. */
		const place = restingPlace({ width: 2560, height: 1400 }, 9 / 16);

		expect(place.height).toBeGreaterThan(place.width);
		expect(place.x + place.width).toBe(2560 - 16);
		expect(place.y + place.height).toBe(1400 - 16);
	});

	it('is brought back inside a window it no longer fits', () => {
		const place = fit({ x: 1200, y: 700, width: 384, height: 216 }, { width: 800, height: 600 });

		expect(place.x + place.width).toBeLessThanOrEqual(800);
		expect(place.y + place.height).toBeLessThanOrEqual(600);
	});

	it('never goes off the top or the left either', () => {
		const place = fit({ x: -400, y: -80, width: 384, height: 216 }, WINDOW);

		expect(place.x).toBe(0);
		expect(place.y).toBe(0);
	});

	it('never goes above the room it is given, which is not always the top of the window', () => {
		/* The desktop shell draws a title strip above the application. A panel placed at zero is a
		   panel behind that strip, and the strip is a drag region, so the gesture that would pull
		   it back out moves the WINDOW instead. `top` is zero in a browser, where there is no strip,
		   which is why every other case here passes no `top`. */
		const place = fit({ x: 100, y: -80, width: 384, height: 216 }, { ...WINDOW, top: 36 });

		expect(place.y).toBe(36);
	});

	it('keeps a panel below the strip even when there is no room for it', () => {
		// A window so short that the panel cannot fit under the strip at all. It stays below the
		// strip rather than sliding up behind it, which is the direction to fail in: half a panel
		// off the bottom can still be dragged, and a panel behind the strip cannot.
		const place = fit(
			{ x: 0, y: 0, width: 384, height: 216 },
			{ width: 800, height: 200, top: 36 }
		);

		expect(place.y).toBe(36);
	});

	it('stops shrinking before it stops being a picture', () => {
		const place = fit({ x: 0, y: 0, width: 40, height: 20 }, WINDOW);

		expect(place.width).toBe(MIN_WIDTH);
		expect(place.height).toBe(MIN_HEIGHT);
	});

	it('is not made bigger than the window it is in', () => {
		const place = fit({ x: 0, y: 0, width: 5000, height: 5000 }, { width: 600, height: 400 });

		expect(place.width).toBe(600);
		expect(place.height).toBe(400);
	});

	it('still fits a window smaller than the smallest panel', () => {
		// A phone in landscape, or a window dragged down to nothing. It stops at the floor rather
		// than inverting into a negative size.
		const place = fit({ x: 0, y: 0, width: 384, height: 216 }, { width: 120, height: 90 });

		expect(place.width).toBe(MIN_WIDTH);
		expect(place.height).toBe(MIN_HEIGHT);
		expect(place.x).toBe(0);
	});
});

describe('what it remembers', () => {
	it('opens where it was last left', () => {
		mini.settle({ x: 40, y: 60, width: 500, height: 280 }, WINDOW);
		mini.close();

		mini.open({ id: 'asset-1' }, WINDOW);

		expect(mini.place).toEqual({ x: 40, y: 60, width: 500, height: 280 });
		expect(mini.showing).toBe(true);
	});

	it('forgets what was playing, and only that', () => {
		// A reload is a new sitting. A panel that came back playing something from before would be a
		// surprise; one that came back in the wrong corner would be an annoyance.
		mini.open({ id: 'asset-1' }, WINDOW);
		const place = mini.place;
		mini.close();

		expect(mini.showing).toBe(false);
		expect(mini.asset).toBeNull();
		mini.open({ id: 'asset-2' }, WINDOW);
		expect(mini.place).toEqual(place);
	});

	it('does not write down a place the window forced on it', () => {
		/* Squeezed in by a narrow window, then opened on a wide one: it goes back where it was put
		 * rather than staying where it was pushed. */
		mini.settle({ x: 800, y: 500, width: 384, height: 216 }, WINDOW);
		mini.open({ id: 'asset-1' }, WINDOW);

		mini.reflow({ width: 700, height: 500 });
		expect(mini.place.x).toBeLessThan(800);

		mini.close();
		mini.open({ id: 'asset-1' }, WINDOW);
		expect(mini.place.x).toBe(800);
	});

	it('opens in the corner when what was stored is not a place', () => {
		localStorage.setItem('sift.mini.place', '{"x":"over there"}');

		mini.open({ id: 'asset-1' }, WINDOW);

		expect(mini.place).toEqual(restingPlace(WINDOW));
	});
});

describe('resizing by an edge rather than a corner', () => {
	const from = { x: 100, y: 100, width: 400, height: 300 };

	it('moves the far edge and leaves the near one where it is', () => {
		// Dragging the bottom right makes it bigger without moving it.
		expect(resized(from, 'se', 60, 40)).toEqual({ x: 100, y: 100, width: 460, height: 340 });
	});

	it('moves the panel when the edge being dragged is the top or the left', () => {
		/* The opposite edge is what stays put, which is the whole of what makes a grip feel like
		 * that grip: dragging the top left up and out grows the panel towards the pointer. */
		expect(resized(from, 'nw', -50, -30)).toEqual({ x: 50, y: 70, width: 450, height: 330 });
	});

	it('changes one dimension when an edge is dragged, not both', () => {
		/* The reason the edges need arithmetic of their own. A pointer travelling down the screen
		 * also travels sideways, and a naive version widens the panel while somebody is dragging
		 * its bottom edge. */
		expect(resized(from, 's', 80, 40), 'the width moved while the bottom edge was dragged').toEqual(
			{ x: 100, y: 100, width: 400, height: 340 }
		);
		expect(resized(from, 'e', 80, 40), 'the height moved while a side was dragged').toEqual({
			x: 100,
			y: 100,
			width: 480,
			height: 300
		});
	});

	it('stops at the minimum rather than dragging the panel along behind it', () => {
		/* Clamped before the edge is moved. The other order keeps moving the panel after its size
		 * has stopped changing, so a panel dragged hard inward walks off across the screen. */
		const squashed = resized(from, 'nw', 1000, 1000);
		expect(squashed.width).toBe(MIN_WIDTH);
		expect(squashed.height).toBe(MIN_HEIGHT);
		expect(squashed.x, 'it kept moving after it stopped shrinking').toBe(
			from.x + from.width - MIN_WIDTH
		);
		expect(squashed.y).toBe(from.y + from.height - MIN_HEIGHT);
	});
});

describe('the one fact carried back out of the panel', () => {
	it('is answered once and then forgotten', () => {
		/* It describes ONE arrival. Left set, the next time that clip is opened (from the grid,
		 * hours later), it would come up paused for no reason anybody could see. */
		handover.wasPaused('a-clip');

		expect(handover.take('a-clip')).toBe(true);
		expect(handover.take('a-clip'), 'the flag survived being read').toBe(false);
	});

	it('is not answered for a different clip', () => {
		handover.wasPaused('a-clip');

		expect(handover.take('b-clip'), 'a stale flag paused something else').toBe(false);
		expect(handover.take('a-clip'), 'asking about another clip consumed it').toBe(true);
	});

	it('says nothing when nothing was handed back', () => {
		expect(handover.take('a-clip')).toBe(false);
	});
});

describe('fitting the panel to the picture', () => {
	/* The control that takes the bars off the sides. What can go wrong here is arithmetic, and
	 * arithmetic that is wrong reads exactly like arithmetic that is right. */

	it('comes out the shape of the picture', () => {
		const fitted = shapedTo(
			{ x: 10, y: 20, width: 300, height: 300 },
			{ width: 1920, height: 1080 }
		);

		expect(fitted.width / fitted.height).toBeCloseTo(16 / 9, 2);
	});

	it('keeps about as much panel as there was', () => {
		// The whole reason it is worked out from the area. A wide clip should not become a wide
		// BANNER, and a tall one should not become a column down the side of the screen. Started
		// from a panel big enough that the smallest-panel floor below does not come into it.
		const before = { x: 0, y: 0, width: 600, height: 400 };

		const fitted = shapedTo(before, { width: 1080, height: 1920 });

		expect(fitted.width * fitted.height).toBeCloseTo(before.width * before.height, -3);
	});

	it('makes a portrait clip taller than it is wide', () => {
		const fitted = shapedTo({ x: 0, y: 0, width: 384, height: 216 }, { width: 1080, height: 1920 });

		expect(fitted.height).toBeGreaterThan(fitted.width);
	});

	it('keeps the shape rather than the size when the panel would be too small', () => {
		/* A tall clip in a panel the size it ships at works out narrower than the smallest panel
		 * allowed. Clamping the width afterwards and leaving the height alone would break the one
		 * thing this control promises: a 1968x3840 clip coming out 240x402 for a picture that is
		 * 240x469.
		 */
		const fitted = shapedTo({ x: 0, y: 0, width: 384, height: 216 }, { width: 1968, height: 3840 });

		// The smallest panel is an AREA, not a width and a height, so a tall clip comes out
		// 135x240 where a wide one comes out 240x135. The same panel, turned. Forced to 240 wide, a
		// portrait panel would be three times the size of a landscape one.
		expect(fitted.width * fitted.height).toBeGreaterThanOrEqual(MIN_WIDTH * MIN_HEIGHT - 1);
		expect(Math.min(fitted.width, fitted.height)).toBeGreaterThanOrEqual(MIN_HEIGHT - 1);
		expect(fitted.width / fitted.height).toBeCloseTo(1968 / 3840, 2);
	});

	it('keeps the shape of a wide clip that would be too short', () => {
		const fitted = shapedTo({ x: 0, y: 0, width: 100, height: 40 }, { width: 1920, height: 1080 });

		expect(fitted.height).toBeGreaterThanOrEqual(MIN_HEIGHT);
		expect(fitted.width / fitted.height).toBeCloseTo(16 / 9, 2);
	});

	it('leaves the panel where it is', () => {
		// It changes the shape, not the place. A control that moved the panel as well would be two
		// things happening on one press.
		const fitted = shapedTo(
			{ x: 120, y: 64, width: 384, height: 216 },
			{ width: 800, height: 600 }
		);

		expect(fitted.x).toBe(120);
		expect(fitted.y).toBe(64);
	});

	it('keeps a resting panel in its corner when a tall picture arrives', () => {
		// The panel rests bottom right as a wide box until the picture says its shape. Taken by its
		// top left, a tall GIF would leave it well in from the right edge and flush with the foot.
		const within = { width: 1600, height: 1000 };
		const resting = restingPlace(within);

		const fitted = shapedTo(resting, { width: 375, height: 666 }, within);

		expect(fitted.height).toBeGreaterThan(fitted.width);
		expect(fitted.x + fitted.width).toBe(resting.x + resting.width);
		expect(fitted.y + fitted.height).toBe(resting.y + resting.height);
	});

	it('reopens a panel left tall the size and place it was left', () => {
		// Fitted side by side, the narrow panel a tall GIF left would be widened to the 240 floor a
		// wide panel has and pushed flush with the right edge before the next picture arrived.
		const within = { width: 1280, height: 1000 };
		const tall = { x: 1077, y: 651, width: 187, height: 333 };
		mini.takesTheShape(187 / 333);
		mini.settle(tall, within);
		mini.close();

		mini.open({ id: 'next', mediaType: 'gif' }, within);

		expect(mini.place).toEqual(tall);
	});

	it('keeps a panel up in the top left where it is when given the window', () => {
		const fitted = shapedTo(
			{ x: 120, y: 64, width: 384, height: 216 },
			{ width: 375, height: 666 },
			{ width: 1600, height: 1000 }
		);

		expect(fitted.x).toBe(120);
		expect(fitted.y).toBe(64);
	});

	it('leaves a picture with no size alone', () => {
		// What the browser says before it has read the file's header. A shape derived from zero is a
		// panel of no width at all, which is a panel nobody can get back.
		const before = { x: 5, y: 6, width: 384, height: 216 };

		expect(shapedTo(before, { width: 0, height: 0 })).toEqual(before);
	});
});

describe('a resize that keeps the shape', () => {
	/* No Fit button: the drag itself cannot produce a shape that needs fitting. A button whose
	 * whole job is to undo the last gesture means the gesture was wrong.
	 *
	 * All arithmetic, and arithmetic that is wrong reads exactly like arithmetic that is right,
	 * which is why it is here rather than judged by eye in a panel two hundred pixels across.
	 */

	const WIDE = 16 / 9;
	const TALL = 1080 / 1920;
	const START = { x: 100, y: 100, width: 400, height: 300 };

	it('is unchanged when there is no shape to keep', () => {
		// A Theater wall, and a file whose header has not been read. Both fall through to the free
		// resize, rather than to a guess.
		expect(resized(START, 'e', 80, 0)).toEqual(resized(START, 'e', 80, 0, undefined));
	});

	it('follows the width when a side is dragged', () => {
		const after = resized(START, 'e', 80, 0, WIDE);

		expect(after.width).toBe(480);
		expect(after.width / after.height).toBeCloseTo(WIDE, 2);
	});

	it('follows the height when the top or the bottom is dragged', () => {
		// The other way round, and it is the case that fitting the shape INSIDE the dragged box
		// gets wrong: that answer leaves the untouched axis as the constraint, so the panel sits
		// still under the hand.
		const after = resized(START, 's', 0, 90, WIDE);

		expect(after.height).toBe(390);
		expect(after.width / after.height).toBeCloseTo(WIDE, 2);
	});

	it('follows whichever axis the hand moved further, in PROPORTION, at a corner', () => {
		// 40 across a 400-wide panel is a tenth; 45 down a 300-tall one is more than that. So the
		// height leads, and it must: measured in bare pixels the width would win every diagonal
		// on a panel wider than it is tall, and a drag mostly downward would widen it.
		const after = resized(START, 'se', 40, 45, WIDE);

		expect(after.height).toBe(345);
		expect(after.width / after.height).toBeCloseTo(WIDE, 2);
	});

	it('keeps the corner that was NOT dragged exactly where it was', () => {
		// The whole of what makes a grip feel like that grip: the far edge stays put. Dragging the
		// top-left inward must move x and y, and dragging the bottom-right must not.
		const grown = resized(START, 'se', 80, 0, WIDE);
		expect({ x: grown.x, y: grown.y }).toEqual({ x: 100, y: 100 });

		const pulled = resized(START, 'nw', -80, 0, WIDE);
		expect(pulled.x).toBe(100 - 80);
		expect(pulled.x + pulled.width).toBe(START.x + START.width);
	});

	it('grows rather than losing the shape at the smallest panel', () => {
		// The fault `shapedTo` guards against, in the other direction: a tall clip squeezed down
		// works out narrower than the minimum, and clamping one side without the other gives the
		// right width and the wrong shape, from the one thing whose whole promise is the shape.
		const after = resized({ x: 0, y: 0, width: 260, height: 300 }, 'w', 200, 0, TALL);

		expect(after.width * after.height).toBeGreaterThanOrEqual(MIN_WIDTH * MIN_HEIGHT - 1);
		expect(Math.min(after.width, after.height)).toBeGreaterThanOrEqual(MIN_HEIGHT - 1);
		expect(after.width / after.height).toBeCloseTo(TALL, 2);
	});

	it('refuses a shape that is not one', () => {
		// Zero and negative both mean "nothing has measured this yet", and a shape derived from
		// either is a panel of no width at all.
		expect(resized(START, 'e', 80, 0, 0)).toEqual(resized(START, 'e', 80, 0));
	});
});

describe('the panel never grows bars back', () => {
	/* The other half of shape-keeping, and it only shows at the very end of a drag: a window
	 * clamp that cut the height and left the width would bring the bars back at exactly the
	 * moment somebody was making the panel as large as it would go.
	 */

	const TALL = 1080 / 1920;
	const WIDE = 16 / 9;
	const SMALL_WINDOW = { width: 1000, height: 500 };

	it('keeps the shape when a drag runs into the bottom of the screen', () => {
		const asked = { x: 0, y: 0, width: 600, height: 1067 };
		const landed = fit(asked, SMALL_WINDOW, TALL);

		expect(landed.height).toBeLessThanOrEqual(SMALL_WINDOW.height);
		expect(landed.width / landed.height).toBeCloseTo(TALL, 2);
	});

	it('keeps the shape when a drag runs into the side', () => {
		const landed = fit({ x: 0, y: 0, width: 4000, height: 2250 }, SMALL_WINDOW, WIDE);

		expect(landed.width).toBeLessThanOrEqual(SMALL_WINDOW.width);
		expect(landed.width / landed.height).toBeCloseTo(WIDE, 2);
	});

	it('a tall panel and a wide one of the same shape reach the same smallest size', () => {
		// Portrait must be able to get as small as landscape.
		const tall = fit({ x: 0, y: 0, width: 1, height: 1 }, SMALL_WINDOW, TALL);
		const wide = fit({ x: 0, y: 0, width: 1, height: 1 }, SMALL_WINDOW, WIDE);

		expect(tall.width * tall.height).toBeCloseTo(wide.width * wide.height, -3);
	});

	it('leaves a panel with no shape clamped side by side', () => {
		// A Theater wall is several clips and has no one shape. Nothing about it changes.
		const landed = fit({ x: 0, y: 0, width: 4000, height: 4000 }, SMALL_WINDOW);

		expect(landed.width).toBe(SMALL_WINDOW.width);
		expect(landed.height).toBe(SMALL_WINDOW.height);
	});
});
