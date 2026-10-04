import type { components } from '$lib/api/schema';
/* Cutting the scrub strip up: which frame belongs to a moment, and where it sits on the sheet.
 *
 * Every frame of a clip's timeline preview is one tile in a single image, so showing the frame
 * under the scrubber is a matter of moving that image behind a window the size of one tile. The
 * arrangement is not derivable from the picture (the tile height varies with the video's shape),
 * so it arrives with the asset and is read from there.
 *
 * Kept out of the component because it is the part that can be silently wrong. A frame off by one
 * looks like a frame, and only the numbers say otherwise.
 */

/** How the strip's frames are laid out, as the server reports it. */
export type SpriteSheet = components['schemas']['SpriteSheet'];

/** A frame's window onto the sheet, ready to be set on an element. */
interface FrameBox {
	/** The size of the window, in pixels. */
	width: number;
	height: number;
	/** The whole sheet, scaled so one tile fills the window. */
	backgroundSize: string;
	/** Which tile that window is over. */
	backgroundPosition: string;
}

/** Whether a layout can be cut up at all. Anything else is treated as no strip. */
export function usable(sheet: SpriteSheet | null | undefined): sheet is SpriteSheet {
	if (!sheet) return false;
	return (
		sheet.columns >= 1 &&
		sheet.rows >= 1 &&
		sheet.frames >= 1 &&
		sheet.frames <= sheet.columns * sheet.rows
	);
}

/**
 * Which frame shows the moment `seconds` into a clip `duration` long.
 *
 * The frames are evenly spread across the clip, so this is the fraction through it. Clamped at both
 * ends: the last frame starts before the last second, and dragging to the very end must show it
 * rather than one cell past it.
 */
export function frameAt(sheet: SpriteSheet, seconds: number, duration: number): number {
	if (!Number.isFinite(seconds) || !Number.isFinite(duration) || duration <= 0) return 0;
	const fraction = Math.min(Math.max(seconds / duration, 0), 1);
	return Math.min(Math.floor(fraction * sheet.frames), sheet.frames - 1);
}

/** Where a frame sits on the sheet, counting across and then down. */
export function cellOf(sheet: SpriteSheet, index: number): { column: number; row: number } {
	const at = Math.min(Math.max(Math.floor(index), 0), sheet.frames - 1);
	return { column: at % sheet.columns, row: Math.floor(at / sheet.columns) };
}

/**
 * The window onto one frame, drawn `width` pixels wide.
 *
 * `natural` is the sheet image's own size, which is where the tile's HEIGHT comes from: only the
 * width is recorded, because the height is whatever the video's shape made it. Dividing the loaded
 * image by the grid gives both exactly, with no aspect ratio to carry around and get wrong.
 */
export function frameBox(
	sheet: SpriteSheet,
	index: number,
	width: number,
	natural: { width: number; height: number }
): FrameBox {
	const tileWidth = natural.width / sheet.columns || sheet.tile_width;
	const tileHeight = natural.height / sheet.rows || tileWidth;
	// The scale the window is drawn at, applied to the whole sheet so the tile lands inside it.
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
