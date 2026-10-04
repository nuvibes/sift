/*
 * How a menu of one thing's rows is placed: whole, wherever the window can hold it.
 *
 * `whole` takes the window's room as the menu's ceiling (the rule lives with `.ui-menu` in
 * `ContextMenu`), so the library flips or slides the menu until all of it fits and it scrolls only
 * when the window itself is shorter. The padding keeps it off the window's edge while it does.
 *
 * One answer for the right-click menu and for every door a button opens, so the two cannot come
 * to open differently. A chooser (a composed list of the library's things) keeps the shared
 * ceiling instead, because it can hold as many rows as the library has.
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
 * Which side of its press a chooser opens on, decided from the room and never from the list.
 *
 * A chooser's surface is capped at the room the library measures on the side it is placed on, and
 * the library flips only a surface that overflows. A list whose rows arrive a moment after it
 * opens is placed while it is short, so it would take the side below; its rows then grow only to
 * that side's room, never overflow, and the flip never runs: a chooser near the window's foot
 * would open with two rows and the whole screen above it free.
 *
 * So the side is chosen at the press: below when the room below holds the whole ceiling, and
 * otherwise whichever side has more room. The same press opens the same way however many rows
 * there are, and the side it takes can always hold the most rows the window allows.
 */
export function chooserSide(room: ChooserRoom): 'top' | 'bottom' {
	const below = room.window - room.bottom;
	const above = room.top - room.chrome;
	if (below >= room.ceiling) return 'bottom';
	return above > below ? 'top' : 'bottom';
}

/** A length token read off the document root, in pixels, or `fallback` when it is not set. */
export function rootLength(name: string, fallback: number): number {
	const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
	return Number.isFinite(value) ? value : fallback;
}
