/*
 * How tall a row of feeds is, so that every feed is the shape of its own picture. Arithmetic, since
 * CSS cannot bring a row down when its columns, derived from that height, come out too wide.
 */

import type { Shape } from './layouts';

/** The shape of what one cell is playing, or null while it has nothing or has not been told. */
export type Aspect = number | null;

/** A file that has not said its size: the commonest shape; a wrong guess costs one reflow. */
export const ASSUMED = 16 / 9;

/**
 * The wall's width against its height, worked at a row height of one and without the gaps so it
 * holds at any size; null while nothing has said what it plays.
 */
export function wallAspect(shape: Shape, aspects: readonly Aspect[]): number | null {
	const rows = Math.max(1, shape.rows);
	const cols = Math.max(1, shape.cols);
	if (aspects.every((one) => one === null)) return null;
	const widest = new Array<number>(cols).fill(0);
	shape.slots.forEach((slot, at) => {
		const aspect = aspects[at] ?? ASSUMED;
		const across = Math.max(1, slot.colSpan);
		const down = Math.max(1, slot.rowSpan);
		const share = (down * aspect) / across;
		for (let step = 0; step < across; step += 1) {
			const column = slot.col + step;
			if (column < cols) widest[column] = Math.max(widest[column], share);
		}
	});
	const wide = widest.reduce((sum, one) => sum + one, 0);
	return wide > 0 ? wide / rows : null;
}

/** The height every row gets, in pixels; null while the wall is unmeasured. */
export function feedHeight(
	shape: Shape,
	box: { width: number; height: number },
	aspects: readonly Aspect[],
	gap: number
): number | null {
	if (box.width <= 0 || box.height <= 0) return null;

	// The height a row gets if width is no constraint: the wall, less the gaps between rows.
	const rows = Math.max(1, shape.rows);
	const cols = Math.max(1, shape.cols);
	const tall = (box.height - gap * (rows - 1)) / rows;
	if (tall <= 0) return null;

	// A column is as wide as the widest thing in it; a spanning slot shares its width out.
	const widest = new Array<number>(cols).fill(0);
	shape.slots.forEach((slot, at) => {
		const aspect = aspects[at] ?? ASSUMED;
		const across = Math.max(1, slot.colSpan);
		const down = Math.max(1, slot.rowSpan);
		// A cell spanning two rows is twice as tall, so it is twice as wide at the same aspect.
		const wide = tall * down * aspect + gap * (down - 1);
		const share = (wide - gap * (across - 1)) / across;
		for (let step = 0; step < across; step += 1) {
			const column = slot.col + step;
			if (column < cols) widest[column] = Math.max(widest[column], share);
		}
	});

	const pictures = widest.reduce((sum, one) => sum + one, 0);
	const gaps = gap * (cols - 1);
	if (pictures <= 0) return null;
	if (pictures + gaps <= box.width) return tall;

	// Too wide: only the pictures scale, since the gaps stay the same number of pixels.
	const room = box.width - gaps;
	return room <= 0 ? null : tall * (room / pictures);
}

/** The strip's least height: 16% of the window, never under 72px or over 148px. */
const STRIP_LEAST = { share: 0.16, floor: 72, ceiling: 148 };

/**
 * How tall the strip's previews are: the height the stage leaves unused, from the least height up
 * to a feed in focus, and no taller than lets every preview fit across.
 */
export function stripHeight(
	shape: Shape,
	wall: { width: number; height: number },
	stage: readonly Aspect[],
	previews: readonly Aspect[],
	{ viewport, under, gap }: { viewport: number; under: number; gap: number }
): number {
	const least = Math.floor(
		Math.min(STRIP_LEAST.ceiling, Math.max(STRIP_LEAST.floor, viewport * STRIP_LEAST.share))
	);
	// Worked from the wall's box with the strip at its least, so the two heights never chase.
	const grid = wall.height - gap - least - under;
	const tall = feedHeight(shape, { width: wall.width, height: grid }, stage, gap);
	if (tall === null) return least;
	const rows = Math.max(1, shape.rows);
	const spare = grid - (tall * rows + gap * (rows - 1));
	const across = previews.reduce<number>((sum, one) => sum + (one ?? ASSUMED), 0);
	const fits =
		across > 0 ? (wall.width - gap * (previews.length - 1)) / across : Number.POSITIVE_INFINITY;
	return Math.floor(Math.max(least, Math.min(least + spare, tall, fits)));
}
