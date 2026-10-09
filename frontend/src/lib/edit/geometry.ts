/* The arithmetic behind dragging a picture about, kept out of the component so a test can settle
 * it. Everything is in the pixels of the picture as it is currently seen. */

export interface Box {
	left: number;
	top: number;
	width: number;
	height: number;
}

export interface Frame {
	width: number;
	height: number;
}

/** Which way round: mirror left to right if `mirrored`, then `quarters` turns clockwise. */
export interface Facing {
	quarters: number;
	mirrored: boolean;
}

export const FACING_FORWARD: Facing = { quarters: 0, mirrored: false };

function onItsSide(facing: Facing): boolean {
	return facing.quarters % 2 === 1;
}

export function framed(frame: Frame, facing: Facing): Frame {
	return onItsSide(facing)
		? { width: frame.height, height: frame.width }
		: { width: frame.width, height: frame.height };
}

/* The four buttons as what they do to the state: a mirror after a turn reverses the turns. */

export function turnedRight(facing: Facing): Facing {
	return { ...facing, quarters: (facing.quarters + 1) % 4 };
}

export function turnedLeft(facing: Facing): Facing {
	return { ...facing, quarters: (facing.quarters + 3) % 4 };
}

export function mirroredAcross(facing: Facing): Facing {
	return { quarters: (4 - facing.quarters) % 4, mirrored: !facing.mirrored };
}

/** Top becomes bottom: a mirror across and a half turn. */
export function mirroredDown(facing: Facing): Facing {
	return { quarters: (6 - facing.quarters) % 4, mirrored: !facing.mirrored };
}

export function turnSteps(facing: Facing): ('mirror' | 'right' | 'half' | 'left')[] {
	const steps: ('mirror' | 'right' | 'half' | 'left')[] = [];
	if (facing.mirrored) steps.push('mirror');
	if (facing.quarters === 1) steps.push('right');
	else if (facing.quarters === 2) steps.push('half');
	else if (facing.quarters === 3) steps.push('left');
	return steps;
}

/* A crop rectangle travels with the picture: a turn means the same part of it, turned. */

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

/** The whole picture: what "no crop" means and what a rectangle is reset to. */
export function wholeOf(frame: Frame): Box {
	return { left: 0, top: 0, width: frame.width, height: frame.height };
}

export function isWhole(box: Box, frame: Frame): boolean {
	return (
		box.left === 0 && box.top === 0 && box.width === frame.width && box.height === frame.height
	);
}

export type Grip = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move';

/** Every handle in drawing order; corners first, so they sit above the edges. */
export const GRIPS: Grip[] = ['nw', 'ne', 'se', 'sw', 'n', 'e', 's', 'w'];

/** The smallest rectangle a drag may leave: an odd size is rounded down by the server. */
export const SMALLEST = 2;

export function gripAt(grip: Grip): { x: number; y: number } {
	const across = grip.includes('w') ? 0 : grip.includes('e') ? 1 : 0.5;
	const down = grip.includes('n') ? 0 : grip.includes('s') ? 1 : 0.5;
	return { x: across, y: down };
}

/** How far a handle is pulled inside the point it marks, so the frame never clips it. */
export function gripShift(grip: Grip): { x: string; y: string } {
	const at = gripAt(grip);
	const pull = (fraction: number) => (fraction === 0 ? '0' : fraction === 1 ? '-100%' : '-50%');
	return { x: pull(at.x), y: pull(at.y) };
}

/** The shapes a rectangle can be locked to: the ones people publish in; Free is none. */
export const SHAPES = [
	{ id: 'free', label: 'Free', ratio: null },
	{ id: 'square', label: 'Square', ratio: 1 },
	{ id: 'portrait', label: '4:5', ratio: 4 / 5 },
	{ id: 'tall', label: '9:16', ratio: 9 / 16 },
	{ id: 'wide', label: '16:9', ratio: 16 / 9 }
] as const;

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

/** The rectangle held to a shape: the handle decides which side gives way; it shrinks to fit. */
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

/* Whole pixels: a fraction names nothing that can be cropped. Clamped again after rounding. */
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

/** A width dragged from a corner with the shape locked, never larger than the picture. */
export function resizedTo(width: number, frame: Frame): number {
	return Math.round(clamp(width, SMALLEST, frame.width));
}

function clamp(value: number, low: number, high: number): number {
	return Math.min(Math.max(value, low), Math.max(low, high));
}
