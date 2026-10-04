/* The arithmetic behind the editor's gestures.
 *
 * The gestures themselves belong in a browser: a handle dragged by a person is a pointer, an
 * element and a layout, and no unit test settles any of that. What they MEAN is all here, and all
 * of it fails silently rather than loudly when it is wrong: a crop that comes out of the wrong part
 * of the picture is still a picture, and a rectangle that stops following a turn still looks like a
 * rectangle.
 */
import { describe, expect, it } from 'vitest';
import {
	FACING_FORWARD,
	SMALLEST,
	boxMirroredAcross,
	boxMirroredDown,
	boxTurnedLeft,
	boxTurnedRight,
	dragged,
	fitted,
	framed,
	gripShift,
	isWhole,
	mirroredAcross,
	mirroredDown,
	moved,
	shaped,
	resizedTo,
	turnSteps,
	turnedLeft,
	turnedRight,
	wholeOf,
	type Box,
	type Facing,
	type Frame
} from './geometry';

const FRAME: Frame = { width: 800, height: 600 };

describe('which way round the picture is', () => {
	it('comes back to where it started after four turns either way', () => {
		let facing = FACING_FORWARD;
		for (let each = 0; each < 4; each += 1) facing = turnedRight(facing);
		expect(facing).toEqual(FACING_FORWARD);
		for (let each = 0; each < 4; each += 1) facing = turnedLeft(facing);
		expect(facing).toEqual(FACING_FORWARD);
	});

	it('undoes a mirror when the same mirror is pressed twice', () => {
		expect(mirroredAcross(mirroredAcross(FACING_FORWARD))).toEqual(FACING_FORWARD);
		expect(mirroredDown(mirroredDown(FACING_FORWARD))).toEqual(FACING_FORWARD);
	});

	it('reverses the turns already made when a mirror is pressed', () => {
		// The one that is easy to get wrong. Turning right and THEN mirroring leaves the picture
		// where mirroring and then turning LEFT leaves it. So a mirror has to flip the direction
		// of what has already been done, or the next press of "turn right" goes the way the person
		// did not expect.
		expect(mirroredAcross(turnedRight(FACING_FORWARD))).toEqual(
			turnedLeft(mirroredAcross(FACING_FORWARD))
		);
	});

	it('reaches exactly eight states and never a ninth', () => {
		// Every sequence of the four buttons, walked until nothing new turns up. Eight is the whole
		// of what a picture can be put through by turning and mirroring it, and every one of them is
		// reachable: a ninth would be a state the server has no way of being told about, and seven
		// would be a button that cannot get somewhere.
		const presses = [turnedRight, turnedLeft, mirroredAcross, mirroredDown];
		const name = (facing: Facing) => `${facing.quarters}${facing.mirrored}`;
		const seen = new Map<string, Facing>([[name(FACING_FORWARD), FACING_FORWARD]]);
		const waiting: Facing[] = [FACING_FORWARD];
		while (waiting.length > 0) {
			const facing = waiting.pop() as Facing;
			for (const press of presses) {
				const next = press(facing);
				expect(next.quarters).toBeGreaterThanOrEqual(0);
				expect(next.quarters).toBeLessThan(4);
				if (seen.has(name(next))) continue;
				seen.set(name(next), next);
				waiting.push(next);
			}
		}
		expect(seen.size).toBe(8);
	});

	it('swaps the sides on a quarter turn and not on a half', () => {
		expect(framed(FRAME, turnedRight(FACING_FORWARD))).toEqual({ width: 600, height: 800 });
		expect(framed(FRAME, turnedRight(turnedRight(FACING_FORWARD)))).toEqual(FRAME);
		expect(framed(FRAME, mirroredAcross(FACING_FORWARD))).toEqual(FRAME);
	});

	it('says nothing when the picture has not been turned at all', () => {
		expect(turnSteps(FACING_FORWARD)).toEqual([]);
	});

	it('puts the mirror before the turn, which is the order the server applies them in', () => {
		expect(turnSteps(mirroredAcross(FACING_FORWARD))).toEqual(['mirror']);
		expect(turnSteps({ quarters: 1, mirrored: true })).toEqual(['mirror', 'right']);
		expect(turnSteps({ quarters: 2, mirrored: false })).toEqual(['half']);
		expect(turnSteps({ quarters: 3, mirrored: false })).toEqual(['left']);
	});
});

describe('a rectangle travels with the picture', () => {
	const BOX: Box = { left: 10, top: 20, width: 100, height: 50 };

	it('comes back to itself after four turns', () => {
		// The failure this catches is a rectangle that drifts a few pixels each turn, which looks
		// like nothing at all until somebody turns a photograph twice.
		let box = BOX;
		let frame = FRAME;
		for (let each = 0; each < 4; each += 1) {
			box = boxTurnedRight(box, frame);
			frame = { width: frame.height, height: frame.width };
		}
		expect(box).toEqual(BOX);
		expect(frame).toEqual(FRAME);
	});

	it('turns back the other way', () => {
		const turned = boxTurnedRight(BOX, FRAME);
		expect(boxTurnedLeft(turned, { width: FRAME.height, height: FRAME.width })).toEqual(BOX);
	});

	it('stays inside the picture when it is turned', () => {
		const turned = boxTurnedRight(BOX, FRAME);
		expect(turned.left).toBeGreaterThanOrEqual(0);
		expect(turned.top).toBeGreaterThanOrEqual(0);
		expect(turned.left + turned.width).toBeLessThanOrEqual(FRAME.height);
		expect(turned.top + turned.height).toBeLessThanOrEqual(FRAME.width);
	});

	it('is mirrored back by the same mirror', () => {
		expect(boxMirroredAcross(boxMirroredAcross(BOX, FRAME), FRAME)).toEqual(BOX);
		expect(boxMirroredDown(boxMirroredDown(BOX, FRAME), FRAME)).toEqual(BOX);
	});

	it('keeps the whole picture whole through every one of them', () => {
		// Somebody who has cropped nothing and turns the picture has still cropped nothing, and a
		// rectangle that is a pixel short of the whole is a crop nobody asked for.
		const whole = wholeOf(FRAME);
		const turned = { width: FRAME.height, height: FRAME.width };
		expect(isWhole(boxTurnedRight(whole, FRAME), turned)).toBe(true);
		expect(isWhole(boxMirroredAcross(whole, FRAME), FRAME)).toBe(true);
		expect(isWhole(boxMirroredDown(whole, FRAME), FRAME)).toBe(true);
	});
});

describe('dragging a handle', () => {
	const BOX: Box = { left: 100, top: 100, width: 200, height: 200 };

	it('moves the edge the handle belongs to and leaves the others alone', () => {
		expect(dragged(BOX, 'e', { x: 400, y: 999 }, FRAME)).toEqual({ ...BOX, width: 300 });
		expect(dragged(BOX, 'w', { x: 50, y: 999 }, FRAME)).toEqual({
			...BOX,
			left: 50,
			width: 250
		});
		expect(dragged(BOX, 's', { x: 999, y: 400 }, FRAME)).toEqual({ ...BOX, height: 300 });
		expect(dragged(BOX, 'n', { x: 999, y: 50 }, FRAME)).toEqual({ ...BOX, top: 50, height: 250 });
	});

	it('moves both edges of a corner', () => {
		expect(dragged(BOX, 'se', { x: 400, y: 350 }, FRAME)).toEqual({
			...BOX,
			width: 300,
			height: 250
		});
	});

	it('stops at the edge of the picture rather than going past it', () => {
		const out = dragged(BOX, 'se', { x: 5000, y: 5000 }, FRAME);
		expect(out.left + out.width).toBe(FRAME.width);
		expect(out.top + out.height).toBe(FRAME.height);

		const back = dragged(BOX, 'nw', { x: -500, y: -500 }, FRAME);
		expect(back.left).toBe(0);
		expect(back.top).toBe(0);
	});

	it('stops rather than turning the rectangle inside out', () => {
		// Dragged past the far side, an unclamped rectangle gets a negative width, which reads as
		// it jumping somewhere else on screen, and produces numbers the server has to refuse.
		const crossed = dragged(BOX, 'e', { x: 0, y: 0 }, FRAME);
		expect(crossed.width).toBe(SMALLEST);
		expect(crossed.left).toBe(BOX.left);

		const other = dragged(BOX, 'w', { x: 5000, y: 0 }, FRAME);
		expect(other.width).toBe(SMALLEST);
		expect(other.left + other.width).toBe(BOX.left + BOX.width);
	});

	it('slides the whole rectangle without changing its size, and not off the edge', () => {
		expect(moved(BOX, { x: 20, y: -30 }, FRAME)).toEqual({ ...BOX, left: 120, top: 70 });

		const shoved = moved(BOX, { x: 5000, y: 5000 }, FRAME);
		expect(shoved.width).toBe(BOX.width);
		expect(shoved.height).toBe(BOX.height);
		expect(shoved.left + shoved.width).toBe(FRAME.width);
		expect(shoved.top + shoved.height).toBe(FRAME.height);
	});
});

describe('dragging the corner of the whole picture', () => {
	it('never makes it bigger than it already is', () => {
		// Enlarging adds no detail and the server refuses it, so the handle stops rather than the
		// refusal arriving afterwards.
		expect(resizedTo(5000, FRAME)).toBe(FRAME.width);
	});

	it('never goes below what can be cut at all', () => {
		expect(resizedTo(-40, FRAME)).toBe(SMALLEST);
	});

	it('rounds to a whole number of pixels', () => {
		expect(resizedTo(320.6, FRAME)).toBe(321);
	});
});

describe('where a handle sits', () => {
	it('is pulled inside the rectangle at every edge and corner', () => {
		// Straddling the edge, a handle on a rectangle covering the whole picture hangs half of
		// itself over the frame, where the frame's own clipping takes it. And that is the state
		// the panel opens in.
		expect(gripShift('nw')).toEqual({ x: '0', y: '0' });
		expect(gripShift('se')).toEqual({ x: '-100%', y: '-100%' });
		expect(gripShift('ne')).toEqual({ x: '-100%', y: '0' });
		expect(gripShift('sw')).toEqual({ x: '0', y: '-100%' });
	});

	it('leaves the middle of an edge centred, which is the only place it can be', () => {
		expect(gripShift('n')).toEqual({ x: '-50%', y: '0' });
		expect(gripShift('s')).toEqual({ x: '-50%', y: '-100%' });
		expect(gripShift('w')).toEqual({ x: '0', y: '-50%' });
		expect(gripShift('e')).toEqual({ x: '-100%', y: '-50%' });
	});
});

describe('a rectangle is whole pixels', () => {
	/* The one that would let nothing be saved at all. A pointer moves in the pixels of a screen
	   and the rectangle is measured in the pixels of the picture, so all but a few drags land on
	   a fraction. And a fractional edge is refused by the shape check, which the panel can
	   only report as being unable to work out what the edit would do. Fixtures that all divide
	   exactly would notice nothing. */
	const TALL = { width: 450, height: 800 };

	it('rounds an edge dragged to a fraction of a pixel', () => {
		const box = dragged(wholeOf(TALL), 'se', { x: 436.1111111111111, y: 423.6457824707031 }, TALL);
		expect(box).toEqual({ left: 0, top: 0, width: 436, height: 424 });
	});

	it('rounds a rectangle slid by a fraction of a pixel', () => {
		const box = moved({ left: 10, top: 10, width: 100, height: 100 }, { x: 12.4, y: -3.7 }, TALL);
		expect(box).toEqual({ left: 22, top: 6, width: 100, height: 100 });
	});

	it('keeps a rounded edge inside the picture', () => {
		// Rounding a number already at the far edge is exactly what would push it over.
		const box = dragged(wholeOf(TALL), 'se', { x: 449.7, y: 799.8 }, TALL);
		expect(box.left + box.width).toBeLessThanOrEqual(TALL.width);
		expect(box.top + box.height).toBeLessThanOrEqual(TALL.height);
	});

	it('gives every side of a dragged rectangle a whole number', () => {
		for (const grip of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const) {
			const box = dragged(wholeOf(TALL), grip, { x: 137.37, y: 291.91 }, TALL);
			for (const side of [box.left, box.top, box.width, box.height]) {
				expect(Number.isInteger(side)).toBe(true);
			}
		}
	});
});

describe('a rectangle held to a shape', () => {
	const WIDE = { width: 1600, height: 1200 };

	it('fits the largest square it can, in the middle', () => {
		expect(fitted(WIDE, 1)).toEqual({ left: 200, top: 0, width: 1200, height: 1200 });
	});

	it('leaves the whole picture alone when no shape is asked for', () => {
		expect(fitted(WIDE, null)).toEqual(wholeOf(WIDE));
	});

	it('follows the width when a side handle is what moved', () => {
		const box = shaped({ left: 0, top: 0, width: 800, height: 1200 }, 'e', WIDE, 1);
		expect(box.width).toBe(box.height);
		expect(box.width).toBe(800);
	});

	it('makes the rectangle smaller rather than push it off the picture', () => {
		// A square asked for at the bottom edge, where there is only 200 left to grow into.
		const box = shaped({ left: 0, top: 1000, width: 900, height: 200 }, 'e', WIDE, 1);
		expect(box.top + box.height).toBeLessThanOrEqual(WIDE.height);
		expect(box.width).toBe(200);
	});

	it('grows away from the handle when the handle is a far corner', () => {
		const box = shaped({ left: 400, top: 300, width: 600, height: 100 }, 'nw', WIDE, 1);
		// The bottom right corner is where it was; the top left is what moved.
		expect(box.left + box.width).toBe(1000);
		expect(box.top + box.height).toBe(400);
		expect(box.width).toBe(box.height);
	});
});
