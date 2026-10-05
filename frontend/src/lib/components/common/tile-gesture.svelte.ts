/*
 * The gesture that turns clicks on tiles into a selection, shared by everything made of tiles.
 *
 * One copy, because the fiddly part is easy to get wrong: a long press ends in a click, which would
 * open the tile it just picked (`#held`). The rules:
 *
 *   - Nothing selected: a plain click opens. Ctrl, Cmd or Shift picks instead, and a long press
 *     picks, which is the whole of touch support.
 *   - Hold, then KEEP THE BUTTON DOWN AND MOVE: everything crossed is picked as one run.
 *   - A run PAINTS in the direction its first tile sets (picked unpicks, unpicked picks), and
 *     coming back undoes the overshoot. See `Selection.beginRun`.
 *   - Something selected: a plain click adds and removes; clearing is the way out.
 *   - Something selected and the PRESS MOVES: another run, no hold. Alt keeps the drag.
 *   - Shift means a run from the anchor; anything else toggles one.
 */

import { matches } from '$lib/shell/shortcuts';

import type { Selection } from './selection.svelte';

/**
 * How long a press has to last to mean "select" rather than "open": past an ordinary click, short
 * of wondering. Also `--press-hold` in `app.css`, which times the cue; `design/interaction.test.ts`
 * refuses a mismatch.
 */
export const PRESS_HOLD_MS = 300;

/**
 * How far the pointer may drift and still be holding still, in pixels: a resting hand moves, and a
 * drag or a scroll covers this in its first few pixels.
 */
export const SLOP = 8;

/** Set on the pressed element while a hold is being counted, so the stylesheet can show it. */
const HOLDING = 'data-holding';

/**
 * The attribute a surface puts on each of its rows so a sweep can tell what it is over: the pointer
 * is elsewhere by then, so the id is read off the DOM under it. A wall without it still gets the
 * hold; its sweep simply picks nothing.
 */
export const TILE_ID = 'data-tile-id';

export class TileGesture {
	#selection: Selection;
	/** The ids currently on screen, in the order they are drawn. Shift-runs are computed against it. */
	#order: () => string[];
	#timer: ReturnType<typeof setTimeout> | null = null;
	/* Whether the press that is finishing was the one that started a selection. See the header. */
	#held = false;
	/** Where the press began, so a drift past the slop can be told from a hand that is not still. */
	#from: { x: number; y: number } | null = null;
	/** What was pressed, so the cue comes off whatever it went on. */
	#pressed: HTMLElement | null = null;
	/**
	 * Whether the hold has fired and the pointer is still down: the sweep. Not `#held`, which the
	 * next click clears; this is cleared by letting go.
	 */
	#sweeping = false;
	/** The last row the sweep picked to, so crossing the same one twice is not four extendTo calls. */
	#sweptTo: string | null = null;
	/**
	 * The tile a BULK sweep (no hold, something already picked) was pressed on, until the pointer
	 * moves off it, so a press that never moves stays a plain click. Null once the run begins.
	 */
	#runFrom: string | null = null;

	constructor(selection: Selection, order: () => string[]) {
		this.#selection = selection;
		this.#order = order;
	}

	pressStart(id: string, event: PointerEvent): void {
		// Only the primary button: right is the context menu's, middle the browser's.
		if (event.button !== 0) return;
		this.#held = false;
		this.#runFrom = null;
		this.pressEnd();

		/*
		 * Already picking: the press is the sweep, with no hold to sit through and no drag.
		 *
		 * Otherwise the browser would drag the tile and a hold would call `only`, dropping the
		 * rest of the selection. Alt keeps the drag, the one way to drop a whole selection
		 * somewhere. `#sweeping` starts at the press, since `#refuseDrag` must listen before the
		 * browser decides a drag has begun.
		 */
		if (this.#selection.count > 0 && !event.altKey) {
			this.#runFrom = id;
			this.#sweptTo = id;
			this.#startSweeping();
			return;
		}

		this.#from = { x: event.clientX, y: event.clientY };
		/* Held, since by the time the press ends the pointer may be somewhere else. */
		this.#pressed = event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
		this.#pressed?.setAttribute(HOLDING, '');

		/* A pointer that WANDERS is a drag and a page that SCROLLS is somebody scrolling; both end
		 * the press, heard on the window, and are removed in `pressEnd`. */
		window.addEventListener('pointermove', this.#moved);
		window.addEventListener('scroll', this.#scrolled, { passive: true, capture: true });

		this.#timer = setTimeout(() => {
			// Select first: the click on the way up still has to be caught by `#held`.
			this.#selection.only(id);
			this.#held = true;
			/* From here a wandering pointer is the sweep. `pressEnd` removes the press's listeners
			   by reference, so it runs before the sweep's own are added. */
			this.pressEnd();
			this.#sweptTo = id;
			this.#startSweeping();
		}, PRESS_HOLD_MS);
	}

	/* Arming the sweep, from the hold or a press while something is picked: written once, so both
	   paths refuse the browser's drag alike. */
	#startSweeping(): void {
		this.#sweeping = true;
		window.addEventListener('pointermove', this.#swept);
		window.addEventListener('pointerup', this.#stopSweeping);
		window.addEventListener('pointercancel', this.#stopSweeping);
		/* And the drag this is about to look like. See `#refuseDrag`. */
		window.addEventListener('dragstart', this.#refuseDrag, true);
	}

	pressEnd(): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		this.#from = null;
		this.#pressed?.removeAttribute(HOLDING);
		this.#pressed = null;
		window.removeEventListener('pointermove', this.#moved);
		window.removeEventListener('scroll', this.#scrolled, { capture: true });
	}

	/* Arrow functions, so `removeEventListener` finds the same reference and none leak. */
	#moved = (event: PointerEvent) => {
		if (!this.#from) return;
		const drifted =
			Math.abs(event.clientX - this.#from.x) > SLOP ||
			Math.abs(event.clientY - this.#from.y) > SLOP;
		if (drifted) this.pressEnd();
	};

	#scrolled = () => this.pressEnd();

	/*
	 * The sweep: everything the pointer crosses, as one run from where the press began.
	 *
	 * `extendTo`, as a shift-click, so overshooting and coming back unpicks the overshoot.
	 * `elementFromPoint`, since the tiles are absolutely placed and a move's target is unreliable.
	 * The same row twice does nothing.
	 */
	#swept = (event: PointerEvent) => {
		if (!this.#sweeping) return;
		const under = document.elementFromPoint(event.clientX, event.clientY);
		const row = under?.closest(`[${TILE_ID}]`);
		const id = row?.getAttribute(TILE_ID);
		if (!id || id === this.#sweptTo) return;
		this.#sweptTo = id;
		/*
		 * A bulk sweep begins its run at the first tile crossed, from the pressed tile, whose state
		 * sets the run's direction before anything touches it. `#held` goes on, so the click this
		 * press produces does not toggle the starting tile back out.
		 */
		if (this.#runFrom !== null) {
			this.#selection.beginRun(this.#runFrom);
			this.#runFrom = null;
			this.#held = true;
		}
		this.#selection.extendTo(id, this.#order());
	};

	/*
	 * A sweep is not a drag, and the browser cannot tell. This is what stops it trying.
	 *
	 * A native drag pre-empts `pointermove`, and every `<a href>` and `<img>` is draggable. On the
	 * window, in the capture phase, only while sweeping, so a wall's own drag payload is never
	 * built. A reactive `draggable={false}` would be read a frame late.
	 */
	#refuseDrag = (event: Event) => {
		event.preventDefault();
		event.stopPropagation();
	};

	#stopSweeping = () => {
		this.#sweeping = false;
		this.#sweptTo = null;
		this.#runFrom = null;
		/* The run is over; its direction must not linger into the next shift-click. */
		this.#selection.endRun();
		window.removeEventListener('pointermove', this.#swept);
		window.removeEventListener('pointerup', this.#stopSweeping);
		window.removeEventListener('pointercancel', this.#stopSweeping);
		window.removeEventListener('dragstart', this.#refuseDrag, true);
	};

	/** Whether a sweep is running, so a surface can hold off on anything that fights it: true from
	 *  the PRESS while something is picked, since that press cannot be a drag. */
	get sweeping(): boolean {
		return this.#sweeping;
	}

	/** Whether this click is about picking things rather than about opening one. */
	selecting(event: MouseEvent): boolean {
		return this.#selection.count > 0 || event.ctrlKey || event.metaKey || event.shiftKey;
	}

	/**
	 * Escape, from anywhere on the screen: let go of everything.
	 *
	 * Only while something is picked, since Escape belongs to dialogs, menus and boxes too, and not
	 * when something nearer already answered it (`defaultPrevented`: a portalled menu has already
	 * closed by the time the window hears the key). Returns whether it did anything.
	 */
	escaped(event?: KeyboardEvent): boolean {
		if (this.#selection.count === 0) return false;
		if (event?.defaultPrevented) return false;
		this.#selection.clear();
		return true;
	}

	/**
	 * `Ctrl+Z` and `Ctrl+Shift+Z`, from anywhere on the screen: on the SELECTION, never the data, so
	 * nothing reaches the server. Not while somebody is typing, where the box's own undo is wanted.
	 * Returns whether it did anything.
	 */
	undoKeys(event: KeyboardEvent): boolean {
		// Asked of the registry, so the keys are the ones the shortcut list shows.
		if (matches(event, 'select.redo')) return this.#selection.redo();
		if (matches(event, 'select.undo')) return this.#selection.undo();
		// Everything on the page, or nothing: one key for both, as a select-all box behaves.
		if (matches(event, 'select.all')) {
			this.#selection.toggleAll(this.#order());
			return true;
		}
		return false;
	}

	/**
	 * A click that might open something: apply the picking rules, and answer whether it was about
	 * picking rather than about opening. Asked BEFORE the click is applied, or letting go of the
	 * last picked tile would open it. Returns true when the caller should do nothing further.
	 */
	handled(id: string, event: MouseEvent): boolean {
		const picking = this.selecting(event);
		this.clicked(id, event);
		return picking;
	}

	/**
	 * A click on a tile, caught before the tile sees it: call it from a capture-phase handler, or
	 * every click of a selection would also open the asset.
	 */
	clicked(id: string, event: MouseEvent): void {
		if (this.#held) {
			// The press that started the selection: its click must not also open the tile.
			this.#held = false;
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (!this.selecting(event)) return;
		event.preventDefault();
		event.stopPropagation();
		this.#selection.pick(id, event, this.#order());
	}
}
