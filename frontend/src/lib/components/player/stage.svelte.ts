/* The frame an asset is shown in, shared between the thing showing it and the thing controlling it.
 *
 * `MediaStage` owns the box: its size, whether it is fullscreen, and whether the controls have
 * faded out because nobody has moved. The player draws its own bar inside that box and needs three
 * things from it: the element, so a menu can be portalled somewhere that is actually drawn while
 * fullscreen; whether it is fullscreen, so the icon says the right thing; and a way to say "the
 * pointer is on me, do not fade".
 *
 * Passed through context rather than as props, because what needs them is not the stage's direct
 * child: it is whatever the caller put inside, at whatever depth. Threading three props through
 * every layer between would make each of those layers know about fullscreen, which is exactly the
 * thing this exists to keep in one place.
 */

import { getContext, setContext } from 'svelte';

const KEY = Symbol('sift.stage');

interface StageHandle {
	/** The element the browser draws when fullscreen. Null before it is mounted. */
	readonly element: HTMLElement | null;
	/** Whether that element is currently the fullscreen one. Follows the browser, never leads it. */
	readonly isFullscreen: boolean;
	/**
	 * Whether the controls are on screen, or have faded out because nobody is doing anything.
	 *
	 * Read rather than guessed at, and that is the whole reason it is here. Anything the bar OPENS
	 * (a menu, a popover) has to go when the bar goes, or it is left hanging over the picture
	 * with nothing underneath it; and the alternative is a second idle clock kept in step with this
	 * one by hand, which is the fault `holdBar`'s own note already records from the other direction.
	 * One clock, asked.
	 */
	readonly showing: boolean;
	/** Go fullscreen, or come back out. */
	toggleFullscreen(): void;
	/** Something happened: show the controls and restart the idle clock. */
	wake(): void;
	/** The pointer is on the controls. Keep them up until it leaves. */
	hold(event?: PointerEvent): void;
}

export function setStage(handle: StageHandle): void {
	setContext(KEY, handle);
}

/** The stage this is being drawn inside, or null when there is none.
 *
 * Null rather than a throw: the player is also rendered on its own page, and a component that
 * refused to exist outside a stage would make that page impossible for no benefit.
 */
export function getStage(): StageHandle | null {
	return getContext<StageHandle | undefined>(KEY) ?? null;
}
