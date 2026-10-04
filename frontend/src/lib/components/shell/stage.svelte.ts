/* The screen, filling the window, and the bar that comes with it.
 *
 * ## Why the shell owns this rather than the screen that asked for it
 *
 * Filling the screen draws the fullscreen element and everything inside it, and nothing else on the
 * page composites at all. So whatever is fullscreened decides what still exists, and a screen that
 * fullscreened its own contents would take the bar of controls above it off the window, because that
 * bar belongs to the shell and is drawn outside every screen.
 *
 * The element that fills the window is therefore the shell's, and it holds the bar and the screen
 * together. A screen asks; it does not choose the element.
 *
 * ## The bar stays where it is, and goes when nobody is doing anything
 *
 * Not at the bottom, over the screen, though a row of chrome costs a strip of picture: for a wall,
 * covering the feeds is worse than shortening them, because a wall is several pictures and losing
 * the bottom of all of them is losing more than the bar takes. So it stays at the top and in FLOW: the screen under it is shorter while it is there,
 * every feed is still on the window, and they are simply smaller.
 *
 * What it does instead is go away by itself, on the same clock the player's bar uses: a couple of
 * seconds after the last thing anybody did, back the moment the pointer moves. The screen grows into
 * the space as it goes. `B` does the same thing from the keyboard, which is what somebody without a
 * pointer has: an idle clock is meaningless where there is nothing to go idle.
 */

/*
 * The key that brings the bar back is declared in `$lib/shell/shortcuts` as `stage.toggleBar`, with every
 * other shortcut in Sift, so the shell comparing against it and Theater's key sheet printing it
 * read one declaration, which also says what the key does.
 */

import { aboutToChange } from '$lib/components/player/motion';

/** How long the bar stays after the last thing anybody did. The player's bar uses the same number. */
const IDLE_MS = 2500;

/**
 * How near the top or the bottom of the screen the pointer has to be, while `edgeOnly` is on.
 *
 * Exported because the wall's own bar follows the same rule and has to use the same number: two
 * bands of different depths, one for the bar at the top and one for the bar at the bottom, is a
 * screen where the chrome half appears.
 */
export const EDGE_BAND = 96;

class Stage {
	/** The element that fills the window: the bar and the screen, together. Set by the layout. */
	#element: HTMLElement | null = null;

	/**
	 * Whether the shell is filling the window.
	 *
	 * Read from the document rather than set by whoever asked, because Escape leaves fullscreen
	 * without any of this code running: a flag we wrote would say the shell was still filled and
	 * the bar would stay hidden in a window it no longer covers.
	 */
	filling = $state(false);

	/** Whether the bar is away. Only ever true while filling; leaving brings it back. */
	barHidden = $state(false);

	/**
	 * Whether the SCREEN is deciding when this bar is up, rather than the shell's idle clock.
	 *
	 * Off everywhere but Theater. A wall has a bar of its own at the foot of the screen, and on two
	 * separate clocks the top strip would go away while the bottom one was still up, and come back
	 * on a movement the wall's own bar ignored. One of them has to be the answer, and it
	 * is the bottom one: it is the one being reached for.
	 *
	 * While this is on, `wake` does nothing and no clock runs; whoever set it writes `barHidden`.
	 */
	driven = $state(false);

	/**
	 * The bar was sent away by hand (the `B` key) on a screen that is driving its own chrome.
	 *
	 * `toggleBar` must not write `barHidden` directly while a screen is driving: the wall
	 * re-asserts its answer only when that answer changes, and pressing `B` while the chrome was up
	 * changes nothing, so the bar would stay away whatever happened afterwards (`theater.spec.ts`'s
	 * "and any movement brings it back" holds this).
	 *
	 * A flag the driving screen reads, rather than a write behind its back. The screen decides what
	 * to do about it (Theater takes it as "quiet until something stirs"), and whoever raised it
	 * lowers it on the way out.
	 */
	dismissed = $state(false);

	#idle: ReturnType<typeof setTimeout> | null = null;
	/* Whether this is a machine with a pointer that can rest. Read once rather than watched: a
	 * machine does not grow or lose a mouse mid-session. On a touch screen the bar never hides on
	 * its own, because a bar that waits to be hovered is a bar that never comes back. */
	#canHover = false;

	/** The layout says which element this is. Called once, with the box holding the bar and screen. */
	register(element: HTMLElement | null): void {
		this.#element = element;
	}

	/**
	 * The box that fills the window, for anything that has to be drawn INSIDE it.
	 *
	 * A browser filling the screen paints this element and its descendants and nothing else on the
	 * page, so a layer portalled to the end of the document is not hidden while a screen is filled:
	 * it is not drawn. Anything portalled that must survive fullscreen asks for this and portals here
	 * instead. See `ContextMenu.portalTo`, which is the first caller.
	 *
	 * Only while the screen is actually FILLED. Portalling into it the rest of the time would put a
	 * menu inside a box that scrolls and clips, which is the fault portalling exists to avoid:
	 * `filling` is the one condition under which the end of the document is the wrong answer.
	 */
	get whatFillsTheWindow(): HTMLElement | null {
		return this.filling ? this.#element : null;
	}

	/** Start watching the document. Returns the undo, for the layout's own teardown. */
	watch(): () => void {
		this.#canHover = window.matchMedia?.('(hover: hover)')?.matches ?? false;
		const changed = () => {
			/*
			 * The `!== null` is the whole of this line, and leaving it out is a fault that reads as
			 * three different ones.
			 *
			 * Nothing is fullscreen at rest, so `document.fullscreenElement` is null, and the box is
			 * also null until the layout has registered it, which is one effect later than the first
			 * time this runs. `null === null` is TRUE, so without the guard the shell would believe it
			 * was filling the window from the moment it started: the Theater screen would drop its own
			 * heading, because the heading is hidden while filled; the bar would go on the idle clock
			 * in an ordinary window; and sending the wall to the corner would ENTER fullscreen, because
			 * that path leaves fullscreen first and `toggle` asks the document rather than the flag.
			 */
			this.filling = this.#element !== null && document.fullscreenElement === this.#element;
			// Leaving puts the bar back where it belongs. Somebody whose bar had gone quiet while
			// filled did not ask for a windowed screen with no controls on it.
			if (!this.filling) {
				this.barHidden = false;
				this.#stopClock();
				return;
			}
			this.wake();
		};
		/* Anything at all counts, and it is watched on the DOCUMENT rather than on the bar. The
		 * pointer spends nearly all of its time over the feeds rather than over the row of controls,
		 * so a listener on the bar would only ever hear from somebody already reaching for it. */
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
		/* A SCREEN THAT DRIVES ITS OWN CHROME IS TOLD, not written over. See `dismissed`: writing
		   `barHidden` here would leave the bar away for good on a wall. */
		if (this.driven) {
			this.dismissed = !this.dismissed;
			return;
		}
		this.barHidden = !this.barHidden;
		// Dismissing by hand means dismissed. Restarting the clock here would put the bar back a
		// couple of seconds later on the very pointer movement that reached the control.
		if (this.barHidden) this.#stopClock();
		else this.wake();
	}

	/**
	 * Something happened: put the bar back, and start counting again.
	 *
	 * Nothing at all while a screen is `driven`: that screen writes `barHidden` itself, and a clock
	 * running underneath it would put the bar back a couple of seconds after the screen took it
	 * away.
	 */
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

/**
 * Keep an element DRAWN while a screen fills the window, as an attachment, for a layer that lives
 * outside the box that fills it.
 *
 * `whatFillsTheWindow` above is the question a PORTAL asks at the moment it opens: a menu or a
 * tooltip is created when it is wanted and can be put in the right place then. A layer that is
 * always on the page cannot ask once. The toaster is the case: it is drawn once for the whole app,
 * after the shell, and left outside, every toast raised while the screen is filled would be counted
 * down and dismissed without a pixel of it on screen.
 *
 * So the element is MOVED into the filled box for as long as the window is filled, and put back
 * where it was the moment it is not. Read inside the attachment, so the move follows `filling` in
 * both directions on its own: an attachment runs as an effect and runs again when what it read
 * changes, tearing down (moving back) first. A placeholder holds its place in the document so the
 * way back is exact rather than "the end of its parent".
 *
 * Moving rather than rendering a second copy inside the box: a second toaster would be a second
 * live region announcing every message twice, and a queue drawn in two places.
 */
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
