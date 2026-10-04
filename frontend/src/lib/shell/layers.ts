/*
 * Which layer a keystroke belongs to.
 *
 * ## The fault this exists for
 *
 * A panel that listens for Escape on the WINDOW hears every Escape on the page, including the ones
 * meant for something drawn on top of it. So declining the confirm before a folder is removed
 * would close Settings as well, and the screen somebody was working in would go with it.
 * `stopPropagation` cannot help: a window listener is the last stop, so by the time it runs there
 * is nothing left to stop.
 *
 * ## The rule, and the one that looks right and is not
 *
 * A sheet that answers Escape calls `preventDefault` on it. So a keystroke that arrives here
 * already prevented was answered by a layer above, and this panel is not what it was aimed at.
 * Bubbling is what makes that reliable rather than a race: a sheet listens on the DOCUMENT and this
 * listens on the WINDOW, and the document is always reached first.
 *
 * "Is focus inside this panel" reads better and is WRONG: a sheet puts focus back on whatever
 * opened it, and it does so while the Escape is still being delivered, so by the time the window
 * hears it focus is already back on a button inside the panel. That test says "yes, mine" on the
 * exact keystroke it exists to refuse.
 */

/**
 * Whether a key event reaching the window still belongs to this panel.
 *
 * False once something above has answered it. Nothing here reads the DOM: what a layer above did
 * with the keystroke is a fact about the EVENT, and asking the document instead means guessing
 * which of several open things the person was in.
 */
export function keystrokeIsUnanswered(event: KeyboardEvent): boolean {
	return !event.defaultPrevented;
}
