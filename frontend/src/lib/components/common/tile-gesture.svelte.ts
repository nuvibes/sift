/* Clicks on tiles as a selection, for everything made of tiles. With nothing picked a click opens;
 * a modifier or a long press picks, and holding then moving picks a run. With something picked a
 * click toggles and a moving press is a run (Alt keeps the drag). A long press's click never
 * opens the tile it picked. */

import { matches } from '$lib/shell/shortcuts';

import type { Selection } from './selection.svelte';

/** A press this long selects; it matches `--press-hold` (design/interaction.test.ts). */
export const PRESS_HOLD_MS = 300;

/** How far a still hand may drift, in pixels. */
export const SLOP = 8;

/** Set on the pressed element while a hold is being counted, so the stylesheet can show it. */
const HOLDING = 'data-holding';

/** Each row's id attribute, read off the DOM under a sweep. */
export const TILE_ID = 'data-tile-id';

export class TileGesture {
	#selection: Selection;
	/** The ids currently on screen, in the order they are drawn. Shift-runs are computed against it. */
	#order: () => string[];
	#timer: ReturnType<typeof setTimeout> | null = null;
	/* Whether the press that is finishing was the one that started a selection. See the header. */
	#held = false;
	#from: { x: number; y: number } | null = null;
	#pressed: HTMLElement | null = null;
	/** The hold fired and the pointer is still down; cleared by letting go. */
	#sweeping = false;
	/** The last row the sweep picked to, so crossing the same one twice is not four extendTo calls. */
	#sweptTo: string | null = null;
	/** The tile a bulk sweep was pressed on, until the pointer leaves it. */
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
		 * Already picking: the press is the sweep, with no hold and no drag (Alt keeps the drag).
		 */
		if (this.#selection.count > 0 && !event.altKey) {
			this.#runFrom = id;
			this.#sweptTo = id;
			this.#startSweeping();
			return;
		}

		this.#from = { x: event.clientX, y: event.clientY };
		this.#pressed = event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
		this.#pressed?.setAttribute(HOLDING, '');

		/* A wandering pointer or a scroll ends the press. */
		window.addEventListener('pointermove', this.#moved);
		window.addEventListener('scroll', this.#scrolled, { passive: true, capture: true });

		this.#timer = setTimeout(() => {
			// Select first: the click on the way up still has to be caught by `#held`.
			this.#selection.only(id);
			this.#held = true;
			/* The press's listeners go before the sweep's are added. */
			this.pressEnd();
			this.#sweptTo = id;
			this.#startSweeping();
		}, PRESS_HOLD_MS);
	}

	#startSweeping(): void {
		this.#sweeping = true;
		window.addEventListener('pointermove', this.#swept);
		window.addEventListener('pointerup', this.#stopSweeping);
		window.addEventListener('pointercancel', this.#stopSweeping);
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
	 * The sweep: every tile crossed, as a shift-click run (`elementFromPoint`: tiles are absolute).
	 */
	#swept = (event: PointerEvent) => {
		if (!this.#sweeping) return;
		const under = document.elementFromPoint(event.clientX, event.clientY);
		const row = under?.closest(`[${TILE_ID}]`);
		const id = row?.getAttribute(TILE_ID);
		if (!id || id === this.#sweptTo) return;
		this.#sweptTo = id;
		/* A bulk sweep's run starts at the pressed tile, whose state sets its direction. */
		if (this.#runFrom !== null) {
			this.#selection.beginRun(this.#runFrom);
			this.#runFrom = null;
			this.#held = true;
		}
		this.#selection.extendTo(id, this.#order());
	};

	/* A sweep is not a drag: refused on the window in the capture phase while sweeping. */
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

	/** Whether a sweep is running, from the press when something is picked. */
	get sweeping(): boolean {
		return this.#sweeping;
	}

	/** Whether this click is about picking things rather than about opening one. */
	selecting(event: MouseEvent): boolean {
		return this.#selection.count > 0 || event.ctrlKey || event.metaKey || event.shiftKey;
	}

	/**
	 * Escape anywhere lets go of everything, while something is picked and nothing nearer answered.
	 */
	escaped(event?: KeyboardEvent): boolean {
		if (this.#selection.count === 0) return false;
		if (event?.defaultPrevented) return false;
		this.#selection.clear();
		return true;
	}

	/** Ctrl+Z and Ctrl+Shift+Z on the selection, never the data; not while typing. */
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

	/** Apply the picking rules before a click opens anything; true when the caller should stop. */
	handled(id: string, event: MouseEvent): boolean {
		const picking = this.selecting(event);
		this.clicked(id, event);
		return picking;
	}

	/** A click on a tile, from a capture-phase handler. */
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
