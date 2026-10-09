/* The screen filling the window, and its bar. The shell owns the filled element because only it
 * is drawn, and the bar lives outside every screen. The bar stays in flow at the top and hides
 * after idle; `B` toggles it for anyone without a pointer. */

// The `B` key is declared in `$lib/shell/shortcuts` as `stage.toggleBar`.

import { aboutToChange } from '$lib/components/player/motion';

/** The same idle time the player's bar uses. */
const IDLE_MS = 2500;

/** Pointer band at the top or bottom while `edgeOnly` is on; the wall's bar shares it. */
export const EDGE_BAND = 96;

class Stage {
	/** The element that fills the window: the bar and the screen, together. Set by the layout. */
	#element: HTMLElement | null = null;

	/** Read from the document, because Escape leaves fullscreen without this code running. */
	filling = $state(false);

	/** Whether the bar is away. Only ever true while filling; leaving brings it back. */
	barHidden = $state(false);

	/** The screen (Theater) decides when the bar is up, so it and the wall's bar keep one clock. */
	driven = $state(false);

	/** Set by `B` while `driven`: the wall re-asserts only on change, so it reads this. */
	dismissed = $state(false);

	#idle: ReturnType<typeof setTimeout> | null = null;
	// Read once. On touch the bar never hides: a bar that waits for a hover would never come back.
	#canHover = false;

	register(element: HTMLElement | null): void {
		this.#element = element;
	}

	/** The filled box, for portals that must be drawn inside it; null when not filled. */
	get whatFillsTheWindow(): HTMLElement | null {
		return this.filling ? this.#element : null;
	}

	/** Start watching the document. Returns the undo, for the layout's own teardown. */
	watch(): () => void {
		this.#canHover = window.matchMedia?.('(hover: hover)')?.matches ?? false;
		const changed = () => {
			// Both sides are null at rest and before `register`, which would read as filling.
			this.filling = this.#element !== null && document.fullscreenElement === this.#element;
			// Leaving fullscreen never leaves a windowed screen without its bar.
			if (!this.filling) {
				this.barHidden = false;
				this.#stopClock();
				return;
			}
			this.wake();
		};
		// Watched on the document: the pointer is over the feeds, rarely over the bar.
		const moved = () => this.wake();
		document.addEventListener('fullscreenchange', changed);
		document.addEventListener('pointermove', moved);
		document.addEventListener('keydown', moved);
		changed();
		return () => {
			document.removeEventListener('fullscreenchange', changed);
			document.removeEventListener('pointermove', moved);
			document.removeEventListener('keydown', moved);
			this.#stopClock();
		};
	}

	/** Fill the window with the bar and the screen, or stop. */
	toggle(): void {
		if (document.fullscreenElement) {
			void document.exitFullscreen?.();
			return;
		}
		// Where the wall stands, for the movement from there into the screen (`screenChanges`).
		aboutToChange();
		void this.#element?.requestFullscreen?.();
	}

	/** Send the bar away, or bring it back. Means nothing unless the shell is filled. */
	toggleBar(): void {
		if (!this.filling) return;
		// A screen that drives its own chrome is told, not overwritten; see `dismissed`.
		if (this.driven) {
			this.dismissed = !this.dismissed;
			return;
		}
		this.barHidden = !this.barHidden;
		// Restarting the clock would bring the bar back on the movement that reached the control.
		if (this.barHidden) this.#stopClock();
		else this.wake();
	}

	/** Show the bar and restart the idle clock; nothing while `driven`, as the screen owns it. */
	wake(): void {
		if (!this.filling) return;
		if (this.driven) return;
		this.barHidden = false;
		this.#stopClock();
		if (!this.#canHover) return;
		this.#idle = setTimeout(() => (this.barHidden = true), IDLE_MS);
	}

	#stopClock(): void {
		if (this.#idle) clearTimeout(this.#idle);
		this.#idle = null;
	}
}

export const stage = new Stage();

// Moved, not copied: a second toaster would announce every message twice.
/** Attachment: moves an always-present layer (the toaster) into the filled box while filling. */
export function drawnWhileFilled(node: HTMLElement): (() => void) | undefined {
	const box = stage.whatFillsTheWindow;
	if (box === null || box.contains(node)) return undefined;
	const place = document.createComment('drawn while filled');
	node.before(place);
	box.append(node);
	return () => {
		place.replaceWith(node);
	};
}
