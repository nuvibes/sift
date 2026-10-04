/* The arithmetic behind dragging a picture about, kept out of the component that draws it.
 *
 * Pointer gestures cannot be settled by a unit test (they are a browser and a person), but
 * everything the gestures MEAN can be, and this is that part. A handle dragged past the opposite
 * edge, a crop rectangle that has to follow the picture when it is turned, the eight ways round a
 * picture can end up after four buttons are pressed in any order: all of it is arithmetic, and all
 * of it is silently wrong rather than broken when it is wrong.
 *
 * Everything here is in the pixels of the picture as it is currently SEEN. Nothing knows about the
 * element on screen; the component converts a pointer position into these and converts these back
 * into percentages to draw with.
 */

/** A rectangle over the picture, from its top left. */
export interface Box {
	left: number;
	top: number;
	width: number;
	height: number;
}

/** How wide and tall the picture currently is. */
export interface Frame {
	width: number;
	height: number;
}

/**
 * Which way round the picture has been put.
 *
 * Any sequence of the four buttons lands on one of eight results, and this is that result rather
 * than a list of what was pressed. Read as: mirror the source left to right if `mirrored`, then
 * turn it `quarters` quarter-turns clockwise.
 */
export interface Facing {
	quarters: number;
	mirrored: boolean;
}

/** Not turned and not mirrored, which is where every picture starts. */
export const FACING_FORWARD: Facing = { quarters: 0, mirrored: false };

/** Whether the picture is currently on its side, so its width and height are swapped. */
function onItsSide(facing: Facing): boolean {
	return facing.quarters % 2 === 1;
}

/** The picture's size once it is put this way round. */
export function framed(frame: Frame, facing: Facing): Frame {
	return onItsSide(facing)
		? { width: frame.height, height: frame.width }
		: { width: frame.width, height: frame.height };
}

/*
 * The four buttons, as what they do to the state.
 *
 * A mirror after a turn is not the same as a turn after a mirror, which is why these are worked
 * out rather than counted. Turning right and then mirroring leaves the picture in the same place
 * as mirroring and then turning LEFT. So pressing mirror has to reverse the turns already made,
 * or the next press of "turn right" goes the way the person did not expect.
 */

export function turnedRight(facing: Facing): Facing {
	return { ...facing, quarters: (facing.quarters + 1) % 4 };
}

export function turnedLeft(facing: Facing): Facing {
	return { ...facing, quarters: (facing.quarters + 3) % 4 };
}

/** Left becomes right, on the picture as it is now. */
export function mirroredAcross(facing: Facing): Facing {
	return { quarters: (4 - facing.quarters) % 4, mirrored: !facing.mirrored };
}

/** Top becomes bottom, which is a mirror across and a half turn. */
export function mirroredDown(facing: Facing): Facing {
	return { quarters: (6 - facing.quarters) % 4, mirrored: !facing.mirrored };
}

/** What the server is told, in the order it has to be applied. Nothing at all when unchanged. */
export function turnSteps(facing: Facing): ('mirror' | 'right' | 'half' | 'left')[] {
	const steps: ('mirror' | 'right' | 'half' | 'left')[] = [];
	if (facing.mirrored) steps.push('mirror');
	if (facing.quarters === 1) steps.push('right');
	else if (facing.quarters === 2) steps.push('half');
	else if (facing.quarters === 3) steps.push('left');
	return steps;
}

/*
 * A crop rectangle has to travel with the picture.
 *
 * Somebody who has drawn a rectangle and then presses "turn right" means the same part of the
 * picture, turned, not the same numbers against a picture that is now a different shape. Left
 * where it was, a rectangle down the left edge of a landscape photograph ends up across the top of
 * a portrait one, over something else entirely.
 */

export function boxTurnedRight(box: Box, frame: Frame): Box {
	return {
		left: frame.height - box.top - box.height,
		top: box.left,
		width: box.height,
		height: box.width
	};
}

export function boxTurnedLeft(box: Box, frame: Frame): Box {
	return {
		left: box.top,
		top: frame.width - box.left - box.width,
		width: box.height,
		height: box.width
	};
}

export function boxMirroredAcross(box: Box, frame: Frame): Box {
	return { ...box, left: frame.width - box.left - box.width };
}

export function boxMirroredDown(box: Box, frame: Frame): Box {
	return { ...box, top: frame.height - box.top - box.height };
}

/** The whole picture, which is what "no crop" means and what a rectangle is reset to. */
export function wholeOf(frame: Frame): Box {
	return { left: 0, top: 0, width: frame.width, height: frame.height };
}

/** Whether a rectangle is the whole picture, so nothing needs cropping. */
export function isWhole(box: Box, frame: Frame): boolean {
	return (
		box.left === 0 && box.top === 0 && box.width === frame.width && box.height === frame.height
	);
}

/** The eight places a rectangle can be taken hold of, and the ninth that moves the whole thing. */
export type Grip = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move';

/** Every handle, in the order they are drawn. Corners first, so they sit above the edges. */
export const GRIPS: Grip[] = ['nw', 'ne', 'se', 'sw', 'n', 'e', 's', 'w'];

/**
 * The smallest rectangle a drag may leave.
 *
 * Two pixels rather than one: a rectangle has to land on the picture's colour blocks, so an odd
 * number is rounded down by the server and one pixel across becomes none across.
 */
export const SMALLEST = 2;

/** Where the handle sits on the rectangle, as a fraction of it. Drives both drawing and dragging. */
export function gripAt(grip: Grip): { x: number; y: number } {
	const across = grip.includes('w') ? 0 : grip.includes('e') ? 1 : 0.5;
	const down = grip.includes('n') ? 0 : grip.includes('s') ? 1 : 0.5;
	return { x: across, y: down };
}

/**
 * How far a handle is pulled back from the point it marks, as a CSS translate.
 *
 * Held INSIDE the rectangle rather than straddling its edge. Centred on the edge, a handle on a
 * rectangle that covers the whole picture hangs half of itself over the frame's edge, where the
 * frame's own clipping takes it. And a rectangle covering the whole picture is the state the
 * panel opens in.
 */
export function gripShift(grip: Grip): { x: string; y: string } {
	const at = gripAt(grip);
	const pull = (fraction: number) => (fraction === 0 ? '0' : fraction === 1 ? '-100%' : '-50%');
	return { x: pull(at.x), y: pull(at.y) };
}

/**
 * The rectangle after a handle has been dragged to this point.
 *
 * Every edge is clamped into the picture and against the opposite edge, so a handle dragged past
 * the far side stops rather than turning the rectangle inside out, which reads as the rectangle
 * jumping somewhere else, and produces numbers a server has to refuse.
 */
/**
 * The shapes a rectangle can be locked to, and what each is called.
 *
 * Free is the absence of one. The rest are the shapes people actually publish in (a square, a
 * portrait post, a full-height phone screen, and a landscape frame) rather than a list of sizes in
 * pixels, which is a different question and one the resize handle already answers.
 */
export const SHAPES = [
	{ id: 'free', label: 'Free', ratio: null },
	{ id: 'square', label: 'Square', ratio: 1 },
	{ id: 'portrait', label: '4:5', ratio: 4 / 5 },
	{ id: 'tall', label: '9:16', ratio: 9 / 16 },
	{ id: 'wide', label: '16:9', ratio: 16 / 9 }
] as const;

/** The largest rectangle of this shape that fits the picture, in the middle of it. */
export function fitted(frame: Frame, ratio: number | null): Box {
	if (!ratio || frame.width <= 0 || frame.height <= 0) return wholeOf(frame);
	const width = Math.min(frame.width, frame.height * ratio);
	const height = width / ratio;
	return whole(
		{
			left: (frame.width - width) / 2,
			top: (frame.height - height) / 2,
			width,
			height
		},
		frame
	);
}

/**
 * The rectangle held to a shape, moving the edges the drag did not.
 *
 * Which side gives way is decided by the handle: dragging the right edge changes the width, so the
 * height follows it, and a corner does both. The rectangle stays inside the picture: a shape that
 * would push it over an edge is made smaller instead, because the alternative is a rectangle that
 * stops following the pointer with no way to see why.
 */
export function shaped(box: Box, grip: Grip, frame: Frame, ratio: number | null): Box {
	if (!ratio) return box;
	const northish = grip.includes('n');
	const westish = grip.includes('w');

	let width = grip === 'n' || grip === 's' ? box.height * ratio : box.width;
	let height = width / ratio;
	// Nothing may leave the picture, so whichever way it would have to grow decides the ceiling.
	const roomAcross = westish ? box.left + box.width : frame.width - box.left;
	const roomDown = northish ? box.top + box.height : frame.height - box.top;
	width = Math.min(width, roomAcross, roomDown * ratio);
	height = width / ratio;

	return whole(
		{
			left: westish ? box.left + box.width - width : box.left,
			top: northish ? box.top + box.height - height : box.top,
			width,
			height
		},
		frame
	);
}

export function dragged(box: Box, grip: Grip, to: { x: number; y: number }, frame: Frame): Box {
	if (grip === 'move') return box;
	let { left, top, width, height } = box;
	const right = left + width;
	const bottom = top + height;

	if (grip.includes('w')) {
		const wanted = clamp(to.x, 0, right - SMALLEST);
		width = right - wanted;
		left = wanted;
	} else if (grip.includes('e')) {
		width = clamp(to.x, left + SMALLEST, frame.width) - left;
	}
	if (grip.includes('n')) {
		const wanted = clamp(to.y, 0, bottom - SMALLEST);
		height = bottom - wanted;
		top = wanted;
	} else if (grip.includes('s')) {
		height = clamp(to.y, top + SMALLEST, frame.height) - top;
	}
	return whole({ left, top, width, height }, frame);
}

/** The rectangle slid by this much, never off the edge of the picture. */
export function moved(box: Box, by: { x: number; y: number }, frame: Frame): Box {
	return whole(
		{
			...box,
			left: clamp(box.left + by.x, 0, Math.max(0, frame.width - box.width)),
			top: clamp(box.top + by.y, 0, Math.max(0, frame.height - box.height))
		},
		frame
	);
}

/**
 * The same rectangle in whole pixels, still inside the picture.
 *
 * A pointer moves in the pixels of a screen and a rectangle is measured in the pixels of the
 * picture, and the two are almost never the same size. So every drag arrives as a fraction. A
 * fraction of a pixel names nothing that can be cropped, and the shape check refuses it outright,
 * which reaches the screen as a panel that cannot say what any drag would do. Rounded here, at the
 * one place a drag becomes a rectangle, rather than at each of the several places one is read.
 *
 * Rounded after clamping and then held inside the frame again, because rounding a number that was
 * exactly on the edge is what pushes it over.
 */
function whole(box: Box, frame: Frame): Box {
	const left = Math.round(clamp(box.left, 0, Math.max(0, frame.width - SMALLEST)));
	const top = Math.round(clamp(box.top, 0, Math.max(0, frame.height - SMALLEST)));
	return {
		left,
		top,
		width: Math.round(clamp(box.width, SMALLEST, Math.max(SMALLEST, frame.width - left))),
		height: Math.round(clamp(box.height, SMALLEST, Math.max(SMALLEST, frame.height - top)))
	};
}

/**
 * A width dragged from a corner of the whole picture, with the shape locked.
 *
 * One number rather than two, because two numbers is an invitation to squash a picture and the
 * person who wants a different shape wants a crop. Never larger than the picture already is:
 * enlarging adds no detail and the server refuses it, so the handle simply stops.
 */
export function resizedTo(width: number, frame: Frame): number {
	return Math.round(clamp(width, SMALLEST, frame.width));
}

function clamp(value: number, low: number, high: number): number {
	return Math.min(Math.max(value, low), Math.max(low, high));
}
