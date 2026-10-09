import type { components } from '$lib/api/schema';
/* Which frame of the scrub strip belongs to a moment, and where it sits on the sheet. */

export type SpriteSheet = components['schemas']['SpriteSheet'];

interface FrameBox {
	width: number;
	height: number;
	backgroundSize: string;
	backgroundPosition: string;
}

export function usable(sheet: SpriteSheet | null | undefined): sheet is SpriteSheet {
	if (!sheet) return false;
	return (
		sheet.columns >= 1 &&
		sheet.rows >= 1 &&
		sheet.frames >= 1 &&
		sheet.frames <= sheet.columns * sheet.rows
	);
}

/** Clamped, so dragging to the very end shows the last frame. */
export function frameAt(sheet: SpriteSheet, seconds: number, duration: number): number {
	if (!Number.isFinite(seconds) || !Number.isFinite(duration) || duration <= 0) return 0;
	const fraction = Math.min(Math.max(seconds / duration, 0), 1);
	return Math.min(Math.floor(fraction * sheet.frames), sheet.frames - 1);
}

export function cellOf(sheet: SpriteSheet, index: number): { column: number; row: number } {
	const at = Math.min(Math.max(Math.floor(index), 0), sheet.frames - 1);
	return { column: at % sheet.columns, row: Math.floor(at / sheet.columns) };
}

/** `natural` gives the tile's height, which only the loaded image knows. */
export function frameBox(
	sheet: SpriteSheet,
	index: number,
	width: number,
	natural: { width: number; height: number }
): FrameBox {
	const tileWidth = natural.width / sheet.columns || sheet.tile_width;
	const tileHeight = natural.height / sheet.rows || tileWidth;
	const scale = width / tileWidth;
	const height = Math.round(tileHeight * scale);
	const { column, row } = cellOf(sheet, index);
	return {
		width,
		height,
		backgroundSize: `${Math.round(natural.width * scale)}px ${Math.round(natural.height * scale)}px`,
		backgroundPosition: `${-Math.round(column * width)}px ${-Math.round(row * height)}px`
	};
}
