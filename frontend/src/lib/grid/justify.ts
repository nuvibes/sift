import type { components } from '$lib/api/schema';
/*
 * Laying tiles out in justified rows.
 *
 * Every row is one height and every tile keeps its proportions, so a row is filled by varying the
 * widths and scaled to span the container: nothing cropped (uniform cards take the top off
 * vertical video) and a shared baseline (masonry's ragged ends look unfinished). Pure functions,
 * since jsdom has no layout engine to test against anyway.
 */

/**
 * The gap between tiles, in pixels: one number. The stylesheet's token mirrors it, since CSS cannot
 * read this file, and `gutter.test.ts` refuses a mismatch.
 */
export const GRID_GUTTER = 8;

/**
 * The line under each tile on a wall whose rows carry a name of their own (Loops), in pixels: part
 * of the ROW's height, so rows are counted with it. One line of the small body face under a step;
 * the stylesheet takes the height from here.
 */
export const CAPTION_HEIGHT = 24;

/** What the layout needs to know about one item. Anything else is the tile's business. */
export type Layable = Pick<components['schemas']['AssetSummary'], 'id' | 'width' | 'height'>;

interface PlacedTile {
	id: string;
	width: number;
	height: number;
}

export interface LaidOutRow {
	height: number;
	tiles: PlacedTile[];
	/**
	 * Whether this row was filled, or is what was left over: false only on a last row where the
	 * items ran out. It tells "the library ends here" from "not fetched enough yet", which counting
	 * tiles cannot, since two very wide files are a full row.
	 */
	filled: boolean;
}

/*
 * What to assume when a file has not been probed yet: slightly landscape, near a mixed library's
 * average, so the shift when the real shape arrives is small.
 */
export const UNKNOWN_ASPECT = 4 / 3;

/* Proportions outside this are almost certainly a broken file rather than a real shape, and a
 * single one of them would otherwise take a whole row to itself and squash it to nothing. */
const MIN_ASPECT = 0.2;
const MAX_ASPECT = 5;

export function aspectOf(item: Layable): number {
	if (!item.width || !item.height || item.height <= 0) return UNKNOWN_ASPECT;
	return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, item.width / item.height));
}

interface JustifyOptions {
	/** The width the row has to fill, in pixels. */
	containerWidth: number;
	/** The height a row aims for before it is scaled to fit. */
	targetHeight: number;
	/** The space between tiles, and between rows. */
	gutter: number;
}

/**
 * Break items into rows and give every tile its final size. The last row is deliberately *not*
 * stretched, or a lone item would be blown up to the full width.
 */
export function justify(items: readonly Layable[], options: JustifyOptions): LaidOutRow[] {
	const { containerWidth, targetHeight, gutter } = options;
	if (items.length === 0 || containerWidth <= 0 || targetHeight <= 0) return [];

	const rows: LaidOutRow[] = [];
	let current: Layable[] = [];
	let aspectSum = 0;

	for (const item of items) {
		current.push(item);
		aspectSum += aspectOf(item);

		// The width this row would occupy at the target height, gutters included.
		const gutters = gutter * (current.length - 1);
		if (aspectSum * targetHeight + gutters >= containerWidth) {
			rows.push(scaleRow(current, aspectSum, options));
			current = [];
			aspectSum = 0;
		}
	}

	if (current.length > 0) {
		rows.push(lastRow(current, options));
	}

	return rows;
}

/** Scale a full row so it spans the container exactly. */
function scaleRow(items: Layable[], aspectSum: number, options: JustifyOptions): LaidOutRow {
	const { containerWidth, gutter } = options;
	const available = containerWidth - gutter * (items.length - 1);
	const height = Math.max(1, Math.round(available / aspectSum));

	const tiles = items.map((item) => ({
		id: item.id,
		width: Math.max(1, Math.round(aspectOf(item) * height)),
		height
	}));

	// Per-tile rounding leaves the row a few pixels off, a wobbling right edge. The difference is
	// shared a pixel at a time, since dumped on one narrow tile it would visibly squash it.
	const used = tiles.reduce((total, tile) => total + tile.width, 0) + gutter * (items.length - 1);
	let slack = containerWidth - used;
	const step = slack > 0 ? 1 : -1;
	for (let index = 0; slack !== 0 && index < tiles.length * 2; index += 1) {
		const tile = tiles[index % tiles.length];
		if (tile.width + step < 1) continue;
		tile.width += step;
		slack -= step;
	}

	return { height, tiles, filled: true };
}

/**
 * One tile, and exactly where on the wall it goes.
 *
 * `x` and `y` are from the top-left of the wall, gutters already counted in.
 */
interface WallTile {
	id: string;
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface Wall {
	tiles: WallTile[];
	/** How tall the whole wall is, so the box holding it can be given a height. */
	height: number;
}

/**
 * Flatten laid-out rows into one list of tiles that know their own position.
 *
 * One keyed list, because Svelte cannot move a node between each-blocks: with rows of tiles, a tile
 * changing row would be rebuilt, and one new file at the front of a newest-first wall re-rows
 * everything, once a second during an import. Positioned rather than flex-wrapped, since a wrap
 * one pixel off the laid-out width would re-wrap the page.
 */
export function wallOf(rows: readonly LaidOutRow[], gutter: number, caption = 0): Wall {
	const tiles: WallTile[] = [];
	let y = 0;
	for (const row of rows) {
		let x = 0;
		for (const tile of row.tiles) {
			tiles.push({ id: tile.id, x, y, width: tile.width, height: tile.height });
			x += tile.width + gutter;
		}
		// A captioned wall draws its line under the tiles, inside the row's own height.
		y += row.height + caption + gutter;
	}
	// The last row has no gutter under it, or every page would end in a strip of empty space.
	return { tiles, height: Math.max(0, y - gutter) };
}

/*
 * The final, incomplete row: natural size, left-aligned, never stretched. It cannot overflow: a row
 * is left over only because it failed the test that closes a full row.
 */
function lastRow(items: Layable[], options: JustifyOptions): LaidOutRow {
	const height = options.targetHeight;

	return {
		height,
		tiles: items.map((item) => ({
			id: item.id,
			width: Math.max(1, Math.round(aspectOf(item) * height)),
			height
		})),
		filled: false
	};
}

/* ---------------------------------------------------------------------------------------------
 * Paging by whole rows.
 *
 * A page of a fixed number of FILES makes a different number of rows each time, so the pager slides
 * and every page ends ragged. A page is a fixed number of ROWS instead, at the cost of not knowing
 * how many files it holds until they are laid out; what follows is that arithmetic.
 * ------------------------------------------------------------------------------------------
 */

/**
 * How many whole rows of this height fit in this much space: whole ones only, since a cut row reads
 * as "scroll for more". Never zero, or a short window could never turn a page.
 */
export function rowsThatFit(available: number, rowHeight: number, gutter: number): number {
	if (available <= 0 || rowHeight <= 0) return 1;
	// n rows have n-1 gutters: each row costs height+gutter and one gutter is given back.
	return Math.max(1, Math.floor((available + gutter) / (rowHeight + gutter)));
}

/**
 * The row height nearest the one asked for that divides this space into whole rows exactly, so no
 * dead strip or clipped row shows. The row count that fits and the one above it are both tried
 * and the closer wins, since stopping at the first can stretch rows far more than needed.
 */
export function snapRowHeight(
	target: number,
	available: number,
	gutter: number,
	caption = 0
): number {
	if (available <= 0 || target <= 0) return target;

	// The caption is part of the divided space; the height answered is the TILES' alone.
	const exact = (rows: number) => (available - gutter * (rows - 1)) / rows - caption;
	const fits = rowsThatFit(available, target + caption, gutter);
	const candidates = [fits, fits + 1];

	let best = target;
	let closest = SNAP_LIMIT * target;
	for (const rows of candidates) {
		/* Floor, never round: half a pixel rounded up stops the last row fitting, and the page
		   loses a whole row to a rounding error. */
		const height = Math.floor(exact(rows));
		// Shorter than this is a wall of stamps, worse than the dead strip it avoids.
		if (height < MIN_ROW_HEIGHT) continue;
		const error = Math.abs(height - target);
		if (error < closest) {
			closest = error;
			best = height;
		}
	}
	return best;
}

/** Below this a tile is too small to recognise anything in, which is the whole point of the grid. */
const MIN_ROW_HEIGHT = 80;

/*
 * How far the snap above is allowed to move a chosen size, as a fraction of it.
 *
 * Unbounded, two notches can snap to the same height and one does nothing. At 15% each notch is
 * confined to a band of its own (120 in 102..138, 180 in 153..207, 260 in 221..299, 440 in
 * 374..506) and the bands do not touch at any window size; 25% bands would. Where no row count
 * comes within it, the chosen size stands and the fold cuts the bottom row: a small ugliness
 * rather than a control that does nothing.
 */
const SNAP_LIMIT = 0.15;

/**
 * How many screenfuls of rows one page holds: four, a run of scrolling before the pager, in whole
 * screenfuls so a page's height never varies. A large window at small tiles is clamped by the
 * server's 200 (`SERVER_PAGE_CAP`) rather than by this. Written only here: the scrolling body is
 * a `1fr` track, so no token would be read.
 */
export const PAGE_SCREENS = 4;

/*
 * WARNING: WHAT "the same size" MEANS HERE, exactly.
 *
 * A page holds the same number of ROWS, not rows of identical height: a row's height falls out of
 * the proportions in it. The difference over a page is a few pixels and moves nothing, since the
 * pager sits outside the scrolling area; forcing equal heights would crop or leave a ragged margin.
 */
export interface Filled {
	/** The rows to draw. At most `rows`, and every one of them full unless the library ran out. */
	rows: LaidOutRow[];
	/**
	 * How many of the items handed in were actually used: the next page starts this many further
	 * on, never at however many were fetched, or the surplus would be skipped unseen.
	 */
	used: number;
	/**
	 * Whether the page came out full. False means too few were fetched or the library ends here,
	 * which only the caller can tell apart.
	 */
	full: boolean;
	/**
	 * How many items were passed over BEFORE the first row kept. Always zero except when filling
	 * backwards from the end of the library, where it is what the page's own offset starts after.
	 */
	skipped: number;
}

/**
 * Lay items out and keep exactly this many complete rows: items are asked for generously, and the
 * ones past the last row belong to the next page.
 */
export function fillRows(
	items: readonly Layable[],
	options: JustifyOptions & { rows: number }
): Filled {
	const laid = justify(items, options);
	const kept = laid.slice(0, Math.max(1, options.rows));
	const used = kept.reduce((total, row) => total + row.tiles.length, 0);
	// Full means as many rows as asked for, all filled; only the final row can be unfilled.
	const full = kept.length >= options.rows && kept.every((row) => row.filled);
	return { rows: kept, used, full, skipped: 0 };
}

/**
 * The same, anchored at the END: keep the LAST rows rather than the first. How "go to the last
 * page" works, since its size cannot be found by arithmetic. The final short row is kept: there it
 * is the end of the library.
 */
export function fillRowsFromEnd(
	items: readonly Layable[],
	options: JustifyOptions & { rows: number }
): Filled {
	const laid = justify(items, options);
	const wanted = Math.max(1, options.rows);
	const kept = laid.slice(Math.max(0, laid.length - wanted));
	const dropped = laid.slice(0, laid.length - kept.length);
	return {
		rows: kept,
		used: kept.reduce((total, row) => total + row.tiles.length, 0),
		full: kept.length >= wanted,
		// What the page starts AFTER, which the caller adds to the offset it fetched from.
		skipped: dropped.reduce((total, row) => total + row.tiles.length, 0)
	};
}

/**
 * Roughly how many files it takes to fill this many rows. An opening bid, not an answer: a row of
 * phone videos holds three times what a row of screenshots does. `fillRows` says whether it was
 * enough, and the average is measured from what is on screen where there is anything.
 */
export function itemsToFill(
	rows: number,
	options: { containerWidth: number; rowHeight: number; averageAspect?: number }
): number {
	const { containerWidth, rowHeight } = options;
	if (containerWidth <= 0 || rowHeight <= 0) return rows;

	const aspect = Math.min(
		MAX_ASPECT,
		Math.max(MIN_ASPECT, options.averageAspect || UNKNOWN_ASPECT)
	);
	const perRow = containerWidth / (rowHeight * aspect);
	// A sixth more than the estimate: an ordinary page fills in one request without much surplus.
	return Math.max(rows, Math.ceil(rows * perRow * 1.17));
}

/**
 * How many items the average is taken over, however many are loaded. FIXED, because averaging
 * whatever came back makes the estimate depend on its own last answer, and two page sizes can
 * then imply each other for ever. The leading sample is what the first page is made of.
 */
const ASPECT_SAMPLE = 60;

export function averageAspect(items: readonly Layable[]): number {
	if (items.length === 0) return UNKNOWN_ASPECT;
	const sample = items.length > ASPECT_SAMPLE ? items.slice(0, ASPECT_SAMPLE) : items;
	return sample.reduce((total, item) => total + aspectOf(item), 0) / sample.length;
}

/*
 * The row height each breakpoint aims for, before the size control adjusts it: bigger windows get
 * taller rows rather than more of them, so a tile is recognised at a glance.
 */
const BREAKPOINT_HEIGHTS: readonly { minWidth: number; height: number }[] = [
	{ minWidth: 1536, height: 220 },
	{ minWidth: 1280, height: 200 },
	{ minWidth: 1024, height: 180 },
	{ minWidth: 768, height: 160 },
	{ minWidth: 640, height: 140 },
	{ minWidth: 0, height: 120 }
];

/*
 * The sizes the size control offers, smallest first.
 *
 * Rows snap to `available/n`, whose gaps near the top are huge, so two targets only land on
 * different row counts when `available >= t1 * t2 / (t2 - t1)`; with 340 at the top, its notch and
 * the one below would collide on most laptops. `SNAP_LIMIT` keeps each notch in its own band, and 440
 * is what makes those bands disjoint everywhere (`paging.test.ts`). A stored value not on this
 * list reads as null (`rememberedSize`), sizing the tiles to the window until somebody picks again.
 */
export const SIZE_STEPS = [120, 180, 260, 440] as const;
export type SizeStep = (typeof SIZE_STEPS)[number];

export function heightForWidth(viewportWidth: number): number {
	for (const step of BREAKPOINT_HEIGHTS) {
		if (viewportWidth >= step.minWidth) return step.height;
	}
	return BREAKPOINT_HEIGHTS[BREAKPOINT_HEIGHTS.length - 1].height;
}

/**
 * How tall a row of justified tiles should be: the chosen notch, or the window's own answer. One
 * function, since it is the whole of what the slider means for a justified wall. The notch is
 * passed in, so this arithmetic imports nothing reactive.
 */
export function targetRowHeight(step: SizeStep | null, containerWidth: number): number {
	return step ?? heightForWidth(containerWidth);
}

/*
 * The same four notches, said in the units a wall of CARDS is laid out in.
 *
 * Not the media ladder's numbers: a tile is a frame and may be small, while a card must be
 * recognised among forty similar ones. The walls share the control and its words and move the
 * same way. Each is the floor of a column that stretches to fill the row, so the wall fills its
 * width. The card shape is `2 / 3`, since most covers are frames of vertical clips and a wall of
 * faces is conventionally drawn so.
 */
const CARD_WIDTHS: Readonly<Record<SizeStep, number>> = {
	120: 200,
	180: 260,
	260: 320,
	440: 400
};

/*
 * How wide a card on an entity wall is at least, for a chosen notch. Null is "size yourselves to
 * the window", for which the caller keeps its own floor (`EntityGrid`).
 */
export function cardWidthForStep(step: SizeStep | null): number | null {
	return step === null ? null : CARD_WIDTHS[step];
}
