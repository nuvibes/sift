/*
 * How a menu of one thing's rows is placed: whole, wherever the window can hold it. `whole` takes
 * the window's room as its ceiling (the rule lives with `.ui-menu` in `ContextMenu`); a chooser
 * keeps the shared ceiling, because it can hold as many rows as the library has.
 */

/** The props a menu's content takes to open whole. */
export const WHOLE_MENU = { class: 'ui-menu whole', collisionPadding: 8 } as const;

/** The props a chooser's content takes: the shared surface and its shared ceiling. */
export const CHOOSER_MENU = { class: 'ui-menu' } as const;

/** What a chooser's side is decided from: the press's box and the window's height. */
interface ChooserRoom {
	/** The trigger's top and bottom edges, as `getBoundingClientRect` gives them. */
	top: number;
	bottom: number;
	/** The window's height. */
	window: number;
	/** The window's own title strip, which nothing floats over (`--window-chrome`). */
	chrome: number;
	/** The chooser's ceiling (`--menu-max-height`); unbounded when the token cannot be read. */
	ceiling: number;
}

/**
 * Which side of its press a chooser opens on, from the room and never from the list: rows that
 * arrive late never overflow the side a short list took, so the library's flip would never run.
 */
export function chooserSide(room: ChooserRoom): 'top' | 'bottom' {
	const below = room.window - room.bottom;
	const above = room.top - room.chrome;
	if (below >= room.ceiling) return 'bottom';
	return above > below ? 'top' : 'bottom';
}

/** A length token read off the document root, in pixels, or `fallback` when it is not in pixels. */
export function rootLength(name: string, fallback: number): number {
	const said = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
	// A window-relative length is no fixed number of pixels, so it reads as the fallback.
	const value = said.endsWith('px') ? parseFloat(said) : NaN;
	return Number.isFinite(value) ? value : fallback;
}
