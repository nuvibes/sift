/*
 * A menu that is closing answers Escape, until it is gone.
 *
 * Picking a row closes the menu, but the library keeps its content drawn until its animations
 * finish (at least a frame, longer when a quickly opened menu's arrival animation is still
 * running). For that moment the menu is on screen but no longer open, so the library's own Escape
 * handling has let go, and an Escape would reach the window, where a panel such as the file
 * popout's History pane would close. Somebody pressing Escape at a menu they can still see means
 * the menu.
 *
 * The rule is the one `$lib/shell/layers` states: a layer that answers Escape calls `preventDefault` on
 * the document, and a panel listening on the window ignores a prevented keystroke. This
 * gives that answer for the closing moment, from the menu reporting itself closed until the library
 * reports the close complete (`onOpenChangeComplete`), and no longer: a guard that outlived the
 * menu would eat the next Escape aimed at the panel underneath.
 */

export interface ClosingMenu {
	/** Hand the library's `onOpenChange` here. */
	changed(open: boolean): void;
	/** And its `onOpenChangeComplete`. */
	complete(open: boolean): void;
	/** Whether the menu is between those two, for a test to read. */
	readonly closing: boolean;
}

/** Call once in a component that owns a menu root. The listener lives as long as the component. */
export function escapeWhileClosing(): ClosingMenu {
	let closing = $state(false);

	function answer(event: KeyboardEvent): void {
		if (closing && event.key === 'Escape') event.preventDefault();
	}

	$effect(() => {
		document.addEventListener('keydown', answer);
		return () => document.removeEventListener('keydown', answer);
	});

	return {
		changed(open) {
			closing = !open;
		},
		complete(open) {
			if (!open) closing = false;
		},
		get closing() {
			return closing;
		}
	};
}
