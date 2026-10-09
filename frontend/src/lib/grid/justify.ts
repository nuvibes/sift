import type { components } from '$lib/api/schema';
/* Justified rows: one height a row, every tile keeps its proportions; pure functions. */

/** Mirrored by the stylesheet's token; `gutter.test.ts` refuses a mismatch. */
export const GRID_GUTTER = 8;

/** Part of the ROW's height on a wall whose rows carry a name (Loops). */
export const CAPTION_HEIGHT = 24;

export type Layable = Pick<components['schemas']['AssetSummary'], 'id' | 'width' | 'height'>;

interface PlacedTile {
	id: string;
	width: number;
	height: number;
}

export interface LaidOutRow {
	height: number;
	tiles: PlacedTile[];
	/** False only on a last row where the items ran out. */
	filled: boolean;
}

/** For a file not probed yet, near a mixed library's average. */
export const UNKNOWN_ASPECT = 4 / 3;

/* Outside this is a broken file, which would take a row to itself. */
const MIN_ASPECT = 0.2;
const MAX_ASPECT = 5;

export function aspectOf(item: Layable): number {
	if (!item.width || !item.height || item.height <= 0) return UNKNOWN_ASPECT;
	return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, item.width / item.height));
}

interface JustifyOptions {
	containerWidth: number;
	targetHeight: number;
	gutter: number;
}

/** The last row is NOT stretched, or a lone item would fill the width. */
export function justify(items: readonly Layable[], options: JustifyOptions): LaidOutRow[] {
	const { containerWidth, targetHeight, gutter } = options;
	if (items.length === 0 || containerWidth <= 0 || targetHeight <= 0) return [];

	const rows: LaidOutRow[] = [];
	let current: Layable[] = [];
	let aspectSum = 0;

	for (const item of items) {
		current.push(item);
		aspectSum += aspectOf(item);

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

function scaleRow(items: Layable[], aspectSum: number, options: JustifyOptions): LaidOutRow {
	const { containerWidth, gutter } = options;
	const available = containerWidth - gutter * (items.length - 1);
	const height = Math.max(1, Math.round(available / aspectSum));

	const tiles = items.map((item) => ({
		id: item.id,
		width: Math.max(1, Math.round(aspectOf(item) * height)),
		height
	}));

	// Rounding leaves the row a few pixels off; shared a pixel at a time.
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

interface WallTile {
	id: string;
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface Wall {
	tiles: WallTile[];
	height: number;
}

/** One keyed, positioned list, so a tile changing row is moved rather than rebuilt. */
export function wallOf(rows: readonly LaidOutRow[], gutter: number, caption = 0): Wall {
	const tiles: WallTile[] = [];
	let y = 0;
	for (const row of rows) {
		let x = 0;
		for (const tile of row.tiles) {
			tiles.push({ id: tile.id, x, y, width: tile.width, height: tile.height });
			x += tile.width + gutter;
		}
		y += row.height + caption + gutter;
	}
	return { tiles, height: Math.max(0, y - gutter) };
}

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

/* --- Paging by whole ROWS, so the pager does not slide and pages do not end ragged. */

/** Whole rows only, never zero. */
export function rowsThatFit(available: number, rowHeight: number, gutter: number): number {
	if (available <= 0 || rowHeight <= 0) return 1;
	return Math.max(1, Math.floor((available + gutter) / (rowHeight + gutter)));
}

/** The nearest height that divides the space into whole rows; two row counts tried. */
export function snapRowHeight(
	target: number,
	available: number,
	gutter: number,
	caption = 0
): number {
	if (available <= 0 || target <= 0) return target;

	const exact = (rows: number) => (available - gutter * (rows - 1)) / rows - caption;
	const fits = rowsThatFit(available, target + caption, gutter);
	const candidates = [fits, fits + 1];

	let best = target;
	let closest = SNAP_LIMIT * target;
	for (const rows of candidates) {
		/* Floor: rounding up by half a pixel loses a row. */
		const height = Math.floor(exact(rows));
		if (height < MIN_ROW_HEIGHT) continue;
		const error = Math.abs(height - target);
		if (error < closest) {
			closest = error;
			best = height;
		}
	}
	return best;
}

const MIN_ROW_HEIGHT = 80;

/* 15%, so each size notch keeps a band of its own at any window size. */
const SNAP_LIMIT = 0.15;

/** Clamped by the server's 200 (`SERVER_PAGE_CAP`) at small tiles. */
export const PAGE_SCREENS = 4;

/* The same number of ROWS a page, not rows of identical height. */
export interface Filled {
	rows: LaidOutRow[];
	/** The next page starts this many on, never at however many were fetched. */
	used: number;
	full: boolean;
	/** Non-zero only when filling backwards from the end. */
	skipped: number;
}

/** Keeps exactly this many complete rows; the rest belong to the next page. */
export function fillRows(
	items: readonly Layable[],
	options: JustifyOptions & { rows: number }
): Filled {
	const laid = justify(items, options);
	const kept = laid.slice(0, Math.max(1, options.rows));
	const used = kept.reduce((total, row) => total + row.tiles.length, 0);
	const full = kept.length >= options.rows && kept.every((row) => row.filled);
	return { rows: kept, used, full, skipped: 0 };
}

/** Anchored at the END, for the last page. */
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
		skipped: dropped.reduce((total, row) => total + row.tiles.length, 0)
	};
}

/** An opening bid: `fillRows` says whether it was enough. */
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
	return Math.max(rows, Math.ceil(rows * perRow * 1.17));
}

/** FIXED, so the estimate does not depend on its own last answer. */
const ASPECT_SAMPLE = 60;

export function averageAspect(items: readonly Layable[]): number {
	if (items.length === 0) return UNKNOWN_ASPECT;
	const sample = items.length > ASPECT_SAMPLE ? items.slice(0, ASPECT_SAMPLE) : items;
	return sample.reduce((total, item) => total + aspectOf(item), 0) / sample.length;
}

/* Bigger windows get taller rows rather than more of them. */
const BREAKPOINT_HEIGHTS: readonly { minWidth: number; height: number }[] = [
	{ minWidth: 1536, height: 220 },
	{ minWidth: 1280, height: 200 },
	{ minWidth: 1024, height: 180 },
	{ minWidth: 768, height: 160 },
	{ minWidth: 640, height: 140 },
	{ minWidth: 0, height: 120 }
];

/* The size control's notches; 440 makes the snap bands disjoint (`paging.test.ts`). */
export const SIZE_STEPS = [120, 180, 260, 440] as const;
export type SizeStep = (typeof SIZE_STEPS)[number];

export function heightForWidth(viewportWidth: number): number {
	for (const step of BREAKPOINT_HEIGHTS) {
		if (viewportWidth >= step.minWidth) return step.height;
	}
	return BREAKPOINT_HEIGHTS[BREAKPOINT_HEIGHTS.length - 1].height;
}

export function targetRowHeight(step: SizeStep | null, containerWidth: number): number {
	return step ?? heightForWidth(containerWidth);
}

/* The same notches for a wall of CARDS, each the floor of a stretching column, at `2 / 3`. */
const CARD_WIDTHS: Readonly<Record<SizeStep, number>> = {
	120: 200,
	180: 260,
	260: 320,
	440: 400
};

/* Null sizes to the window (`EntityGrid`). */
export function cardWidthForStep(step: SizeStep | null): number | null {
	return step === null ? null : CARD_WIDTHS[step];
}
