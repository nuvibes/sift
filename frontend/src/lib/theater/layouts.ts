import type { components } from '$lib/api/schema';
/* The shape of a wall: a grid, and where each cell sits in it. Adding a feed walks the ladder
 * (`grown`) so a wall is always one the picker offers; every function here is pure. */

/** Where one cell sits: its top-left corner, and how many tracks it covers. All from zero. */
interface Slot {
	row: number;
	col: number;
	rowSpan: number;
	colSpan: number;
}

/** A wall: a grid that size, holding those cells in that order. */
export type Shape = Omit<components['schemas']['ShapeBody'], 'slots'> & { slots: Slot[] };

/** Which way a feed puts another; the ladder ignores it, but a gesture may name a side. */
export type Direction = 'left' | 'right' | 'up' | 'down';

export type LayoutId =
	| 'single'
	| 'side_by_side'
	| 'stacked'
	| 'side_by_side_by_side'
	| 'grid'
	| 'center_stage'
	| 'center_stage_two'
	| 'center_stage_three'
	| 'center_stage_grid';

export interface Layout {
	id: LayoutId;
	label: string;
	/** What the label's letter stands for, shown when a row is pointed at. */
	tooltip?: string;
	shape: Shape;
	/** How many previews this layout opens with under the wall: a strip, not part of the grid. */
	strip?: number;
}

function at(row: number, col: number, rowSpan = 1, colSpan = 1): Slot {
	return { row, col, rowSpan, colSpan };
}

/** How many previews a Stage View layout opens with; `MOST_CELLS` is the real ceiling. */
const MOST_PREVIEWS = 5;
export const MOST_IN_FOCUS = 4;

/* Named rows by columns, except the two pairs: (P) side by side for portrait, (L) stacked for
landscape. */
export const LAYOUTS: readonly Layout[] = [
	/* A cell is a run, so a wall of one is a channel playing whatever a filter matches. */
	{ id: 'single', label: 'Grid 1x1', shape: { rows: 1, cols: 1, slots: [at(0, 0)] } },
	{
		id: 'side_by_side',
		label: 'Grid 1x2 (P)',
		tooltip: 'Portrait',
		shape: { rows: 1, cols: 2, slots: [at(0, 0), at(0, 1)] }
	},
	{
		id: 'side_by_side_by_side',
		label: 'Grid 1x3',
		shape: { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] }
	},
	{
		id: 'stacked',
		label: 'Grid 1x2 (L)',
		tooltip: 'Landscape',
		shape: { rows: 2, cols: 1, slots: [at(0, 0), at(1, 0)] }
	},
	{
		id: 'grid',
		label: 'Grid 2x2',
		shape: { rows: 2, cols: 2, slots: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] }
	},
	/* One in focus and five waiting under it; the shape is the focus half only. */
	{
		id: 'center_stage',
		label: 'Stage View 1x1',
		shape: { rows: 1, cols: 1, slots: [at(0, 0)] },
		strip: MOST_PREVIEWS
	},
	/* The same strip over each of the three walls the picker offers. */
	{
		id: 'center_stage_two',
		label: 'Stage View 1x2',
		shape: { rows: 1, cols: 2, slots: [at(0, 0), at(0, 1)] },
		strip: MOST_PREVIEWS
	},
	{
		id: 'center_stage_three',
		label: 'Stage View 1x3',
		shape: { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] },
		strip: MOST_PREVIEWS
	},
	{
		id: 'center_stage_grid',
		label: 'Stage View 2x2',
		shape: { rows: 2, cols: 2, slots: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] },
		strip: MOST_PREVIEWS
	}
];

/** The most cells a wall may hold: nine keys, and a browser's six connections per origin. */
export const MOST_CELLS = 9;

/* Three side by side: a wall of one is a player with extra steps. */
export const OPENING: Shape = { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] };

/** One layout by name, falling back by name to `OPENING`'s, never to the list's first. */
export function layout(id: string): Layout {
	const wanted = LAYOUTS.find((one) => one.id === id);
	if (wanted) return wanted;
	const opening = LAYOUTS.find((one) => one.id === 'side_by_side_by_side');
	return opening ?? LAYOUTS[0];
}

/** The shape as a `grid-template` value: rows over columns, every track an equal share. */
export function template(shape: Shape): string {
	/*
	 * Rows are the feed height and columns `minmax(0, auto)`, so a feed is the shape of its own
	 * picture and what is left over gathers at the outside of the wall. `1fr` only until the wall
	 * is measured (see `feedHeight`).
	 */
	return `repeat(${shape.rows}, var(--feed-height, 1fr)) / repeat(${shape.cols}, minmax(0, auto))`;
}

/** A `grid-template` for a picture of the shape: every place one square. */
export function pictureOf(shape: Shape, side: string): string {
	return `repeat(${shape.rows}, ${side}) / repeat(${shape.cols}, ${side})`;
}

/** One slot as a `grid-area` value. CSS counts tracks from one; this file counts from zero. */
export function area(slot: Slot): string {
	return `${slot.row + 1} / ${slot.col + 1} / span ${slot.rowSpan} / span ${slot.colSpan}`;
}

function copy(shape: Shape): Shape {
	return { rows: shape.rows, cols: shape.cols, slots: shape.slots.map((one) => ({ ...one })) };
}

/** Whether a slot covers this position. */
function covers(slot: Slot, row: number, col: number): boolean {
	return (
		row >= slot.row &&
		row < slot.row + slot.rowSpan &&
		col >= slot.col &&
		col < slot.col + slot.colSpan
	);
}

/** Whether anything at all covers this position. */
function taken(shape: Shape, row: number, col: number): boolean {
	return shape.slots.some((slot) => covers(slot, row, col));
}

/** Whether every position of this rectangle is free. */
function clear(shape: Shape, row: number, col: number, rowSpan: number, colSpan: number): boolean {
	if (row < 0 || col < 0 || row + rowSpan > shape.rows || col + colSpan > shape.cols) return false;
	for (let r = row; r < row + rowSpan; r += 1) {
		for (let c = col; c < col + colSpan; c += 1) {
			if (taken(shape, r, c)) return false;
		}
	}
	return true;
}

/**
 * Grow cells into any hole, until there are none: by whole edges, in a fixed order (left, up,
 * right, down), so a wall heals the same way every time.
 */
export function fill(shape: Shape): Shape {
	const next = copy(shape);
	let changed = true;
	while (changed) {
		changed = false;
		for (let row = 0; row < next.rows && !changed; row += 1) {
			for (let col = 0; col < next.cols && !changed; col += 1) {
				if (taken(next, row, col)) continue;
				// The slot on each side, and whether its whole edge can move into the space.
				const left = next.slots.find((s) => s.col + s.colSpan === col && covers(s, row, col - 1));
				if (left && clear(next, left.row, col, left.rowSpan, 1)) {
					left.colSpan += 1;
					changed = true;
					continue;
				}
				const above = next.slots.find((s) => s.row + s.rowSpan === row && covers(s, row - 1, col));
				if (above && clear(next, row, above.col, 1, above.colSpan)) {
					above.rowSpan += 1;
					changed = true;
					continue;
				}
				const right = next.slots.find((s) => s.col === col + 1 && covers(s, row, col + 1));
				if (right && clear(next, right.row, col, right.rowSpan, 1)) {
					right.col -= 1;
					right.colSpan += 1;
					changed = true;
					continue;
				}
				const below = next.slots.find((s) => s.row === row + 1 && covers(s, row + 1, col));
				if (below && clear(next, row, below.col, 1, below.colSpan)) {
					below.row -= 1;
					below.rowSpan += 1;
					changed = true;
				}
			}
		}
	}
	return next;
}

/** Drop any track nothing sits in, so removing a cell does not leave a stripe of empty wall. */
export function tighten(shape: Shape): Shape {
	const next = copy(shape);
	for (let row = next.rows - 1; row >= 0; row -= 1) {
		let empty = true;
		for (let col = 0; col < next.cols; col += 1) {
			if (taken(next, row, col)) {
				empty = false;
				break;
			}
		}
		if (!empty) continue;
		for (const slot of next.slots) {
			if (slot.row > row) slot.row -= 1;
			else if (slot.row + slot.rowSpan > row) slot.rowSpan -= 1;
		}
		next.rows -= 1;
	}
	for (let col = next.cols - 1; col >= 0; col -= 1) {
		let empty = true;
		for (let row = 0; row < next.rows; row += 1) {
			if (taken(next, row, col)) {
				empty = false;
				break;
			}
		}
		if (!empty) continue;
		for (const slot of next.slots) {
			if (slot.col > col) slot.col -= 1;
			else if (slot.col + slot.colSpan > col) slot.colSpan -= 1;
		}
		next.cols -= 1;
	}
	return next;
}

/** The ladder: the shapes a wall grows through, indexed by how many cells it has now. */
const LADDER: readonly LayoutId[] = ['single', 'side_by_side', 'side_by_side_by_side', 'grid'];

/**
 * The next shape up the ladder, or null at the top: the count alone decides, so a custom shape
 * straightens. A copy, since `duplicate` moves slots in what it is handed.
 */
export function grown(shape: Shape): Shape | null {
	const rung = LADDER[shape.slots.length];
	if (rung === undefined) return null;
	return copy(layout(rung).shape);
}

/**
 * Take one feed off the wall, keeping the last: the count decides as in `grown`, so a wall comes
 * down the rungs it went up. `tighten` and `fill` serve a custom shape of more than four.
 */
export function remove(shape: Shape, index: number): Shape {
	if (shape.slots.length <= 1 || shape.slots[index] === undefined) return shape;
	const rung = LADDER[shape.slots.length - 2];
	if (rung !== undefined) return copy(layout(rung).shape);
	const next = copy(shape);
	next.slots.splice(index, 1);
	return fill(tighten(next));
}

/** Whether Sift ships this shape; the strip counts, or Center Stage would be a wall of one. */
export function named(shape: Shape, strip = 0): LayoutId | null {
	const same = LAYOUTS.find((one) => same_shape(one.shape, shape) && (one.strip ?? 0) === strip);
	return same?.id ?? null;
}

function same_shape(left: Shape, right: Shape): boolean {
	if (left.rows !== right.rows || left.cols !== right.cols) return false;
	if (left.slots.length !== right.slots.length) return false;
	return left.slots.every((slot, index) => {
		const other = right.slots[index];
		return (
			slot.row === other.row &&
			slot.col === other.col &&
			slot.rowSpan === other.rowSpan &&
			slot.colSpan === other.colSpan
		);
	});
}

/** The shape as the server spells it (`row_span`), since the reader below takes either. */
export type StoredShape = components['schemas']['ShapeBody'];

export function writeShape(shape: Shape): StoredShape {
	return {
		rows: shape.rows,
		cols: shape.cols,
		slots: shape.slots.map((slot) => ({
			row: slot.row,
			col: slot.col,
			row_span: slot.rowSpan,
			col_span: slot.colSpan
		}))
	};
}

/** A stored shape, or null rather than a repair: the caller falls back to its saved layout. */
export function readShape(value: unknown): Shape | null {
	if (typeof value !== 'object' || value === null) return null;
	const held = value as Record<string, unknown>;
	const rows = held.rows;
	const cols = held.cols;
	const slots = held.slots;
	if (typeof rows !== 'number' || typeof cols !== 'number' || !Array.isArray(slots)) return null;
	if (rows < 1 || cols < 1 || slots.length < 1 || slots.length > MOST_CELLS) return null;
	const read: Slot[] = [];
	for (const entry of slots) {
		const made = readSlot(entry);
		if (made === null) return null;
		read.push(made);
	}
	const shape = { rows, cols, slots: read };
	for (const slot of shape.slots) {
		if (slot.rowSpan < 1 || slot.colSpan < 1) return null;
		if (slot.row < 0 || slot.col < 0) return null;
		if (slot.row + slot.rowSpan > rows || slot.col + slot.colSpan > cols) return null;
	}
	if (overlapping(shape)) return null;
	return shape;
}

function readSlot(entry: unknown): Slot | null {
	if (typeof entry !== 'object' || entry === null) return null;
	const slot = entry as Record<string, unknown>;
	const made = {
		row: slot.row,
		col: slot.col,
		rowSpan: slot.row_span ?? slot.rowSpan,
		colSpan: slot.col_span ?? slot.colSpan
	};
	if (Object.values(made).some((one) => typeof one !== 'number')) return null;
	return made as Slot;
}

// Two cells over one square is a wall that draws one on top of the other.
function overlapping(shape: Shape): boolean {
	for (let row = 0; row < shape.rows; row += 1) {
		for (let col = 0; col < shape.cols; col += 1) {
			if (shape.slots.filter((slot) => covers(slot, row, col)).length > 1) return true;
		}
	}
	return false;
}
