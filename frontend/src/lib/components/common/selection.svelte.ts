/* Picking several things out of a grid, one model for every screen: click, ctrl-click and
 * shift-click from an anchor. The order is passed in on each call, since the grid's changes. */

import { untrack } from 'svelte';

/** One step somebody took, held so it can be taken back. */
interface Step {
	ids: ReadonlySet<string>;
	anchor: string | null;
	base: ReadonlySet<string>;
	/* Whether that step's pick reached past the loaded screen. */
	beyond: boolean;
}

/* How many steps back a selection remembers: a convenience, not a record, so bounded. */
const STEPS_KEPT = 50;

export class Selection {
	#ids = $state(new Set<string>());
	#anchor: string | null = null;
	/* What was picked when the anchor was set; a shift-click is this plus the run. */
	#base: ReadonlySet<string> = new Set();

	/* Which way the run paints, decided where it began; null adds. */
	#run: 'pick' | 'unpick' | null = null;

	/*
	 * Whether the pick reaches past the loaded rows (a whole-query pick); reset by other gestures.
	 */
	#beyondScreen = $state(false);

	/* Clicks, never data: one gesture is one step, clearing included. */
	#undo: Step[] = [];
	#redo: Step[] = [];

	get ids(): ReadonlySet<string> {
		return this.#ids;
	}

	/** Whether there is a click to take back, and one to put back. */
	get canUndo(): boolean {
		return this.#undo.length > 0;
	}

	get canRedo(): boolean {
		return this.#redo.length > 0;
	}

	/* The pick copied untracked, so an effect clearing it does not re-run itself. */
	#snapshot(): Step {
		return untrack(() => ({
			ids: new Set(this.#ids),
			anchor: this.#anchor,
			base: new Set(this.#base),
			beyond: this.#beyondScreen
		}));
	}

	#restore(step: Step): void {
		this.#ids = new Set(step.ids);
		this.#anchor = step.anchor;
		this.#base = new Set(step.base);
		this.#beyondScreen = step.beyond;
	}

	/* Remember where we were, before changing it; a new step drops what had been taken back. */
	#step(): void {
		this.#undo.push(this.#snapshot());
		if (this.#undo.length > STEPS_KEPT) this.#undo.shift();
		this.#redo = [];
	}

	/** Take back the last thing that was picked. */
	undo(): boolean {
		const step = this.#undo.pop();
		if (!step) return false;
		this.#redo.push(this.#snapshot());
		this.#restore(step);
		return true;
	}

	/** Put back whatever `undo` just took away. */
	redo(): boolean {
		const step = this.#redo.pop();
		if (!step) return false;
		this.#undo.push(this.#snapshot());
		this.#restore(step);
		return true;
	}

	/** Forget the steps when the screen changes under them. */
	forgetSteps(): void {
		this.#undo = [];
		this.#redo = [];
	}

	get count(): number {
		return this.#ids.size;
	}

	get isEmpty(): boolean {
		return this.#ids.size === 0;
	}

	has(id: string): boolean {
		return this.#ids.has(id);
	}

	/** Whether what is picked reaches past the rows on screen. See `#beyondScreen`. */
	get beyondScreen(): boolean {
		return this.#beyondScreen;
	}

	/** Everything picked in the screen's order, so an action agrees with the screen. */
	ordered(items: readonly string[]): string[] {
		if (this.#beyondScreen) return [...this.#ids];
		const shown = items.filter((id) => this.#ids.has(id));
		if (shown.length === this.#ids.size) return shown;
		const seen = new Set(shown);
		return [...shown, ...[...this.#ids].filter((id) => !seen.has(id))];
	}

	clear(): void {
		// A step, deliberately. Clearing is the gesture most worth being able to take back.
		this.#step();
		this.#ids = new Set();
		this.#anchor = null;
		this.#base = new Set();
		this.#beyondScreen = false;
	}

	/** Pick exactly this one. The plain click. */
	only(id: string): void {
		this.#step();
		this.#ids = new Set([id]);
		this.#anchor = id;
		this.#base = new Set();
		this.#beyondScreen = false;
	}

	/** Declare a run from here and its direction; nothing changes until `extendTo`. */
	beginRun(id: string): void {
		this.#run = this.#ids.has(id) ? 'unpick' : 'pick';
		this.#anchor = id;
		this.#base = new Set(this.#ids);
	}

	/** The hand lifted: forget the run's direction, so the next shift-click does not unpick. */
	endRun(): void {
		this.#run = null;
	}

	/** Add it or take it away, leaving the rest alone. Ctrl-click, or a checkbox. */
	toggle(id: string): void {
		this.#step();
		const next = new Set(this.#ids);
		if (next.has(id)) {
			next.delete(id);
		} else {
			next.add(id);
		}
		this.#ids = next;
		// The anchor follows even on a removal: the next range starts where the hand last was.
		this.#anchor = id;
		this.#base = new Set(next);
		// A click is a pick made on the screen, so it ends a whole-query pick.
		this.#beyondScreen = false;
	}

	/**
	 * The base plus (or minus) the run from the anchor to here, recomputed so overshoot comes back.
	 */
	extendTo(id: string, items: readonly string[]): void {
		const from = this.#anchor === null ? -1 : items.indexOf(this.#anchor);
		const to = items.indexOf(id);

		if (to === -1) return;
		if (from === -1) {
			this.only(id);
			return;
		}

		// One step for the whole range: it was drawn in one movement and it comes back in one.
		this.#step();
		const [start, end] = from <= to ? [from, to] : [to, from];
		const next = new Set(this.#base);
		/* Every tile in the run alike, the anchor included; which treatment is `#run`'s. */
		for (const between of items.slice(start, end + 1)) {
			if (this.#run === 'unpick') next.delete(between);
			else next.add(between);
		}
		this.#ids = next;
		// A range is drawn across the rows on screen, so it is a pick of the screen. See `toggle`.
		this.#beyondScreen = false;
		// The anchor stays where the range began, so the next drag measures from the same end.
	}

	/** A plain click on a picked grid adds and removes, as on a phone. */
	pick(id: string, event: { shiftKey: boolean }, items: readonly string[]): void {
		if (event.shiftKey) {
			this.extendTo(id, items);
			return;
		}
		this.toggle(id);
	}

	/** Pick everything on screen, or drop it all if everything is. */
	toggleAll(items: readonly string[]): void {
		if (items.length > 0 && items.every((id) => this.#ids.has(id))) {
			this.clear();
			return;
		}
		this.#step();
		this.#ids = new Set(items);
		this.#anchor = items.at(-1) ?? null;
		this.#base = new Set(items);
		this.#beyondScreen = false;
	}

	/** Pick every row the whole query matches, loaded or not; no anchor. */
	pickWholeQuery(items: readonly string[]): void {
		this.#step();
		this.#ids = new Set(items);
		this.#anchor = null;
		this.#base = new Set(items);
		this.#beyondScreen = true;
	}

	/** Take these ids as the pick, as a fact (no step, no run), for a mirrored pick (swap mode). */
	hold(ids: readonly string[]): void {
		this.#ids = new Set(ids);
		this.#base = new Set(ids);
		this.#beyondScreen = false;
		this.#run = null;
	}

	/** Forget anything no longer on screen. */
	retain(items: readonly string[]): void {
		/* A whole-query pick is not of the screen, so the screen cannot trim it. */
		if (this.#beyondScreen) return;
		const present = new Set(items);
		const next = new Set([...this.#ids].filter((id) => present.has(id)));
		if (next.size !== this.#ids.size) {
			this.#ids = next;
			// The steps go with the screen; not a step itself, since nobody clicked.
			this.forgetSteps();
		}
		this.#base = new Set([...this.#base].filter((id) => present.has(id)));
		if (this.#anchor !== null && !present.has(this.#anchor)) this.#anchor = null;
	}
}
