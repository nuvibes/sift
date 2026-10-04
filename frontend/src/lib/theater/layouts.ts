import type { components } from '$lib/api/schema';
/* The shape of a wall: a grid, and where each cell sits in it.
 *
 * The layouts are the whole vocabulary. Free-form building (each feed putting another beside,
 * above or below it) produces walls nobody chose: two presses of Add and the shape is an L, or
 * a column of one against a column of three, with no way back to something that reads as a wall
 * except picking a layout again. So adding a feed walks the LADDER instead (see `grown`), and
 * the wall is always one of the four shapes the picker offers.
 *
 * `fill` and `tighten` remain for a narrower reason: removing walks the ladder back down too (see
 * `remove`), so a hole is left only in a wall saved as a custom shape of more than four, which
 * still loads.
 *
 * ## Everything in this file is pure
 *
 * Every function takes a shape and returns a new one. That is what makes the awkward parts
 * (what happens to the hole a removed cell leaves, what a stored shape that does not add up should
 * do) testable without a browser, a wall, or a video anywhere near them.
 */

/** Where one cell sits: its top-left corner, and how many tracks it covers. All from zero. */
interface Slot {
	row: number;
	col: number;
	rowSpan: number;
	colSpan: number;
}

/** A wall: a grid that size, holding those cells in that order. */
export type Shape = Omit<components['schemas']['ShapeBody'], 'slots'> & { slots: Slot[] };

/**
 * Which way a feed puts another one.
 *
 * The ladder ignores it: `Wall.spawn` takes the word and the next predefined shape is the answer
 * whichever side was named. It is kept rather than deleted because a gesture may still have a side
 * in it (a drag, an arrow key), and a caller that knows one should be able to say so without the
 * wall pretending the side decided anything. `Wall`'s own tests hold that to be true.
 */
export type Direction = 'left' | 'right' | 'up' | 'down';

export type LayoutId =
	| 'single'
	| 'side_by_side'
	| 'side_by_side_by_side'
	| 'grid'
	| 'center_stage'
	| 'center_stage_two'
	| 'center_stage_three'
	| 'center_stage_grid';

export interface Layout {
	id: LayoutId;
	label: string;
	shape: Shape;
	/**
	 * How many PREVIEWS this layout opens with, under the wall. Absent means none.
	 *
	 * A preview is a cell like any other (its own feed, its own sound, its own menu) drawn small
	 * in a strip beneath the wall rather than in one of the shape's places. It is here rather than in
	 * the shape because the strip is not part of the grid: aligning a row of five under a wall of
	 * four would need twenty columns, and every column of this grid is sized by what is in it so that
	 * a feed can be the shape of its own picture. A strip of equal thumbnails wants the opposite.
	 */
	strip?: number;
}

function at(row: number, col: number, rowSpan = 1, colSpan = 1): Slot {
	return { row, col, rowSpan, colSpan };
}

/**
 * How many previews a Center stage layout OPENS with, and the most feeds that may be in focus.
 *
 * Five is a starting point rather than a ceiling: a strip capped at five would keep a wall of three
 * in focus from growing past eight while a wall of four sat at nine, the same nine cells with only
 * one arrangement able to use them all. The real ceiling is the
 * wall's own `MOST_CELLS`, and the strip may take whatever the focus half is not using.
 *
 * Four in focus is a ceiling and stays one: it is where a feed stops being big enough to be the
 * thing you are watching, which is true whatever the strip happens to hold.
 */
/* NOT exported: the layouts below are its only readers, and the ceiling a caller wants is
   `MOST_CELLS` or `MOST_IN_FOCUS`. `public-surface.test.ts` refuses an export nothing outside
   the file imports. */
const MOST_PREVIEWS = 5;
export const MOST_IN_FOCUS = 4;

/* Named by shape, rows by columns, as the shapes below are declared: "1x2" is one row of two.
   A shape is a thing you count, and a word per shape would be one more thing to learn per shape.
   Plain ASCII "x" rather than the multiplication sign: the repo refuses non-ASCII in the public
   tree, and the letter reads the same. */
export const LAYOUTS: readonly Layout[] = [
	/* One feed, which is a player with a wall's controls, and worth having for exactly that: a
	   cell is a RUN, so a wall of one is a channel playing whatever a filter matches, forever. */
	{ id: 'single', label: '1x1', shape: { rows: 1, cols: 1, slots: [at(0, 0)] } },
	{
		id: 'side_by_side',
		label: '1x2',
		shape: { rows: 1, cols: 2, slots: [at(0, 0), at(0, 1)] }
	},
	{
		id: 'side_by_side_by_side',
		label: '1x3',
		shape: { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] }
	},
	{
		id: 'grid',
		label: '2x2',
		shape: { rows: 2, cols: 2, slots: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] }
	},
	/*
	 * ONE IN FOCUS AND FIVE WAITING UNDER IT.
	 *
	 * The shape is the FOCUS half only: what the strip holds is the number beside it. It opens on
	 * one big feed because that is what the arrangement is for; the focus half grows to four the
	 * same way every other wall does, by picking a shape, and the strip stays where it is.
	 */
	{
		id: 'center_stage',
		label: 'Center stage 1x1',
		shape: { rows: 1, cols: 1, slots: [at(0, 0)] },
		strip: MOST_PREVIEWS
	},
	/*
	 * THE SAME STRIP, OVER EACH OF THE THREE WALLS.
	 *
	 * The focus half of Center stage is an ordinary wall and can be any shape, so these are the
	 * three the picker already offers with the strip kept underneath. They are here rather than
	 * left to somebody picking Center stage and then a shape, because that is two presses and a
	 * thing to know, and the four together are what 'nine at once' looks like as a choice.
	 */
	{
		id: 'center_stage_two',
		label: 'Center stage 1x2',
		shape: { rows: 1, cols: 2, slots: [at(0, 0), at(0, 1)] },
		strip: MOST_PREVIEWS
	},
	{
		id: 'center_stage_three',
		label: 'Center stage 1x3',
		shape: { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] },
		strip: MOST_PREVIEWS
	},
	{
		id: 'center_stage_grid',
		label: 'Center stage 2x2',
		shape: { rows: 2, cols: 2, slots: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] },
		strip: MOST_PREVIEWS
	}
];

/**
 * The most cells a wall may hold.
 *
 * Nine because the keyboard reaches nine and a three-by-three is the last shape a person can
 * actually watch. It is a real ceiling rather than a shrug: every drawn cell is a live media
 * element, and a browser gives one origin six connections.
 */
export const MOST_CELLS = 9;

/*
 * The wall Sift opens on.
 *
 * Three feeds side by side rather than one. A wall of one is a player with extra steps, and the
 * shapes are the only way to change how many there are, so a wall opens already being a wall.
 */
export const OPENING: Shape = { rows: 1, cols: 3, slots: [at(0, 0), at(0, 1), at(0, 2)] };

/**
 * One layout by name, or the one a wall falls back to when a stored name means nothing here.
 *
 * The fallback is named rather than `LAYOUTS[0]`: following the order of the list, adding Single to
 * its front would silently make a wall whose stored layout this version cannot read open as ONE
 * feed instead of three. The wall Sift opens on is three side by side (see `OPENING`, which says why),
 * and a fallback that follows the order of a list is a fallback that moves when somebody sorts
 * it.
 */
export function layout(id: string): Layout {
	const wanted = LAYOUTS.find((one) => one.id === id);
	if (wanted) return wanted;
	const opening = LAYOUTS.find((one) => one.id === 'side_by_side_by_side');
	// The list always holds it; the fallback of the fallback is the first, so this cannot be null.
	return opening ?? LAYOUTS[0];
}

/** The shape as a `grid-template` value: rows over columns, every track an equal share. */
export function template(shape: Shape): string {
	/*
	 * Rows share the height; COLUMNS are sized by what is in them.
	 *
	 * This is what lets a feed be the shape of its own picture. A track of `1fr` is a fixed width,
	 * so a portrait clip in it either leaves ground down both sides (which is the padding a wall
	 * should not have), or is cropped to fill it, which throws away the sides of the picture.
	 *
	 * `auto` against rows that are a definite height resolves the other way round: the row's height
	 * comes from the wall, the cell declares its own aspect ratio, and the track is exactly as wide
	 * as that makes it. So the feeds are their own shape, they sit against each other with no
	 * ground between them, and whatever is left over is at the outside of the wall where nothing is
	 * missing from. `minmax(0, auto)` rather than bare `auto` so a wall too wide for the window
	 * shrinks instead of running off the edge of it.
	 *
	 * ## THE ROWS ARE THE FEED HEIGHT, NOT `1fr`
	 *
	 * `1fr` is every row taking an equal share of the WALL. A row that had to come down so the
	 * feeds fit across (see `feedHeight`) would then be shorter than its track, and the cell
	 * centred in it, so the height left over would be split into a band above and a band below
	 * EVERY row: black between the two rows of a two-by-two, and the outside edges never reached.
	 * The wall's `align-content: center`, meant to gather all of that at the outside, could do
	 * nothing while the tracks were `1fr` and already filled the box.
	 *
	 * Rows exactly as tall as the feeds: the cells sit against each other, and what is left over is
	 * at the outside of the WALL, together, where nothing is missing from. `1fr` remains as the
	 * fallback for the moment before the wall has been measured. See `feedHeight`, which answers
	 * null until then.
	 *
	 * ## Where that remainder goes is the WALL's business, not this line's
	 *
	 * Centred, at the outside of the wall, in every case. The stage bar rises OVER the foot of the
	 * wall rather than holding a band open under the pictures: a reserved band stays reserved
	 * while the bar is faded out. `TheaterWall`'s stylesheet is where both are written down.
	 */
	return `repeat(${shape.rows}, var(--feed-height, 1fr)) / repeat(${shape.cols}, minmax(0, auto))`;
}

/**
 * The shape as a `grid-template` for a PICTURE of it, at a fixed size per place.
 *
 * `template` above is for the wall itself, where the whole point is that a column is as wide as
 * whatever is in it: `minmax(0, auto)` against rows of a definite height is what lets a feed be
 * the shape of its own picture.
 *
 * A picture of the shape wants the opposite and cannot use it: there is nothing in the tracks to
 * size them from, so `auto` resolves to nothing at all: a two-column picker comes out `0px 0px`
 * with both numbers drawn on top of each other. Every place is the same square here, which is what
 * a diagram of a layout means: it says where the places ARE, not how big they are.
 */
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
 * Grow cells into any hole, until there are none.
 *
 * A hole appears when a cell is taken away: a removed cell leaves a rectangle of nothing in the
 * middle of a wall, which reads as a cell that failed to load rather than as a shape.
 *
 * A cell only ever grows by a whole edge, so the wall stays a grid of rectangles. Left first, then
 * up, then right, then down: an order rather than a rule, but a FIXED one, because a wall that
 * healed differently depending on which hole was looked at first would be unpredictable to build
 * with.
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

/**
 * THE LADDER: the shapes a wall grows through, smallest first.
 *
 * Indexed by how many cells the wall has NOW, so the rung for a wall of two is the shape of three.
 * Four rungs, because the fourth is the last shape with a place for every cell to be big enough to
 * watch: past it a new feed goes into the strip instead, which is the wall's decision rather than
 * this file's (see `Wall.spawn`).
 *
 * Not exported. The order is a product decision about how a wall grows, and the one question a
 * caller has is "what is the next shape", which is `grown`.
 */
const LADDER: readonly LayoutId[] = ['single', 'side_by_side', 'side_by_side_by_side', 'grid'];

/**
 * The next shape up the ladder, or null when this wall is already at the top of it.
 *
 * It is the count that decides and nothing else: not which cell was pressed, not which side was
 * asked for. A wall loaded from a saved custom shape therefore STRAIGHTENS when it is added to: a
 * stacked pair grown by one is a row of three, not a stack of three with a hole to heal. That is
 * the point rather than a side effect. The only shapes reachable by building are the four here.
 *
 * A copy, never the layout itself. `duplicate` slides slots around in what it is handed, and
 * handing out `LAYOUTS`'s own object would let one wall's duplicate rewrite the shape every other
 * wall reads.
 */
export function grown(shape: Shape): Shape | null {
	const rung = LADDER[shape.slots.length];
	if (rung === undefined) return null;
	return copy(layout(rung).shape);
}

/**
 * Take one feed off the wall.
 *
 * The last one stays. A wall of nothing is not a state anybody meant to reach, and there would be
 * nothing left on screen to build back out from.
 *
 * ## Removing walks the LADDER back down, the same rungs adding walks up
 *
 * "Take the slot out, drop any track left empty, and let a neighbour grow over the hole" fails on a
 * two-by-two: one cell out of four empties no whole row and no whole column, so the wall stays two
 * rows by two columns with a neighbour stretched across the gap: two over one, the lone cell
 * centred under a pair, three portrait feeds drawn as a pyramid when the ladder has a shape for
 * three. A wall that goes up through 1x1, 1x2, 1x3, 2x2 and comes down through something else is
 * two rules for one question.
 *
 * So the count decides here exactly as it does in `grown`: when the cells left are a number the
 * ladder has a rung for, the wall IS that rung. Cells keep their order (the rung's places are in
 * reading order, as every layout's are), so the feeds after the removed one move up a place, which
 * is what `Wall.remove` does with the cells themselves.
 *
 * `tighten` and `fill` stay for the counts no rung names. Only a wall loaded from a saved custom
 * shape can hold more than four in focus, and taking one out of that still leaves a hole to heal
 * rather than a shape to pick.
 */
export function remove(shape: Shape, index: number): Shape {
	if (shape.slots.length <= 1 || shape.slots[index] === undefined) return shape;
	const rung = LADDER[shape.slots.length - 2];
	if (rung !== undefined) return copy(layout(rung).shape);
	const next = copy(shape);
	next.slots.splice(index, 1);
	return fill(tighten(next));
}

/**
 * Whether this shape is one Sift ships, so the wall can be stored under its name.
 *
 * The strip is part of the answer and not a detail: Center Stage's focus half is a single feed, which
 * is the same shape as a wall of one with nothing under it. Without the count, opening Center Stage
 * and then dropping every preview would leave a wall still filed under its name.
 */
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

/**
 * A shape from whatever was stored, or null when it cannot be drawn.
 *
 * Null rather than a repair. A stored wall that does not add up is a wall somebody would load and
 * find rearranged, and the caller has a better answer than this does: fall back to the layout the
 * arrangement was also saved under.
 */
/*
 * The shape as the SERVER spells it.
 *
 * `row_span` and `col_span`, not `rowSpan` and `colSpan`. The reader below takes either, which
 * would hide a wrong spelling here: a shape saved with the browser's spelling and read back by the
 * browser round-trips perfectly, and only the server refuses it, with a 422 that reaches the
 * screen as "That wall could not be saved" and nothing else.
 *
 * One function, so there is one place that knows how this is written down.
 */
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
		if (typeof entry !== 'object' || entry === null) return null;
		const slot = entry as Record<string, unknown>;
		const made = {
			row: slot.row,
			col: slot.col,
			rowSpan: slot.row_span ?? slot.rowSpan,
			colSpan: slot.col_span ?? slot.colSpan
		};
		if (Object.values(made).some((one) => typeof one !== 'number')) return null;
		read.push(made as Slot);
	}
	const shape = { rows, cols, slots: read };
	for (const slot of shape.slots) {
		if (slot.rowSpan < 1 || slot.colSpan < 1) return null;
		if (slot.row < 0 || slot.col < 0) return null;
		if (slot.row + slot.rowSpan > rows || slot.col + slot.colSpan > cols) return null;
	}
	// Two cells over one square is a wall that draws one on top of the other.
	for (let row = 0; row < rows; row += 1) {
		for (let col = 0; col < cols; col += 1) {
			if (shape.slots.filter((slot) => covers(slot, row, col)).length > 1) return null;
		}
	}
	return shape;
}
