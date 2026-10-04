/*
 * What a menu tells the rows inside it: where its layers are drawn.
 *
 * Every menu here puts its rows inside a `Scroller` so a long menu does not run off the window, and
 * a scrolling region clips. A submenu is drawn outside the box it belongs to, so unportalled it
 * would be laid out, mounted and clipped to nothing. So it is portalled, like every other layer
 * here.
 *
 * The target is handed down rather than assumed. The end of the document is nearly always right,
 * but a fullscreen browser paints only the fullscreened element's subtree, so a layer portalled to
 * `body` while a wall is filled does not exist on screen. The menu that opened a row takes a
 * `portalTo` for that, and the row cannot ask it, since rows are handed to a menu as a snippet
 * rather than built by it.
 *
 * A context, therefore: the menu says where its layers go, and every row inside it agrees. There is
 * one menu dressing (the compact rows, `--menu-row-*` in `app.css`, on the surface every floating
 * layer has), so a flyout matches its menu with nothing to carry; if a second look were ever
 * wanted, it would travel the same way.
 */
import { getContext, setContext } from 'svelte';

const WHAT_THE_MENU_SAYS = Symbol('sift:menu');

/** Everything a menu tells the rows inside it. Getters, so both follow a changing screen. */
interface MenuSays {
	/** Where the layers this menu owns are drawn. Null is the end of the document. */
	where: () => Element | null | undefined;
}

/** Said by a menu, once, for every row in it. */
export function ownsTheMenu(says: MenuSays): void {
	setContext(WHAT_THE_MENU_SAYS, says);
}

/**
 * Read by a row that opens a flyout.
 *
 * The default is what a row outside any of our own menus gets: the end of the document.
 */
export function theMenu(): MenuSays {
	return (
		getContext<MenuSays | undefined>(WHAT_THE_MENU_SAYS) ?? {
			where: () => null
		}
	);
}
