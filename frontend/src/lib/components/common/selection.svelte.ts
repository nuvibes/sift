/* Picking several things out of a grid.
 *
 * One model for every screen of tiles, so shift-click behaves alike everywhere: a set of ids and
 * an anchor, and the three gestures every file manager has:
 *
 *   click              this one, and nothing else
 *   ctrl/cmd-click     add or remove this one, keep the rest
 *   shift-click        everything between the last one and this one
 *
 * The anchor is the last item picked deliberately, so a range dragged out and back grows and
 * shrinks from the same end. The order of the items is passed in on each call, since the grid's
 * order changes underneath and a copy here would be an older answer.
 */

import { untrack } from 'svelte';

/** One step somebody took, held so it can be taken back. */
interface Step {
	ids: ReadonlySet<string>;
	anchor: string | null;
	base: ReadonlySet<string>;
	/* Whether that step's pick reached past the loaded screen (`#beyondScreen`), so taking back a
	   whole-query pick restores it under its own rules. */
	beyond: boolean;
}

/* How many steps back a selection remembers: a convenience, not a record, so bounded. */
const STEPS_KEPT = 50;

export class Selection {
	#ids = $state(new Set<string>());
	#anchor: string | null = null;
	/* What was picked when the anchor was set, before any range was dragged from it: a shift-click
	 * is this plus the run, recomputed, so dragging back shrinks the range. */
	#base: ReadonlySet<string> = new Set();

	/*
	 * Which way the run in progress paints, decided once, where it began: from an unpicked tile it
	 * picks, from a picked one it unpicks. Decided once and recomputed from `#base` on every move, so
	 * an overshoot is given back in both directions. Null means add (a shift-click, or the sweep a
	 * long press starts through `only`).
	 */
	#run: 'pick' | 'unpick' | null = null;

	/*
	 * Whether what is picked reaches past the rows the screen has loaded.
	 *
	 * For a pick made on the screen, `ordered` and `retain` keep to the loaded rows; a whole-query
	 * pick ("Select 1,000 of 1,250") must keep all of them. A flag set where the pick is made, and
	 * reset by every other gesture, so it is never sticky. `$state` because `ordered` reads it.
	 */
	#beyondScreen = $state(false);

	/* What was picked before each of the last few steps, and what has been taken back.
	 *
	 * THESE HOLD CLICKS, NEVER DATA: nothing here reaches the server. One gesture is one step (a
	 * shift-click of twenty tiles comes back in one press), and clearing counts too, which is where
	 * a way back is most wanted. */
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

	/*
	 * What is picked right now, copied, WITHOUT subscribing to it: screens clear the selection from
	 * inside an effect, and a tracked read here would make that effect re-run itself until Svelte
	 * stops it.
	 */
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

	/**
	 * Forget the steps, when the screen changes underneath (a new question, a page, a reload): the
	 * ids in them name rows that are gone.
	 */
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

	/**
	 * Everything picked, in the order the screen is showing it, never the order it was clicked, so
	 * an action agrees with the screen. A whole-query pick answers all of it in the order read.
	 * Picks not on screen follow the visible ones, in the order picked.
	 */
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

	/**
	 * Declare a run from here, and which way it paints. Nothing is picked or unpicked yet.
	 *
	 * For a sweep begun while things are picked: the tile becomes the anchor, what is picked becomes
	 * the base, and the direction is read off the tile (`#run`). No step: a run that has not moved
	 * changed nothing; the first `extendTo` takes it.
	 */
	beginRun(id: string): void {
		this.#run = this.#ids.has(id) ? 'unpick' : 'pick';
		this.#anchor = id;
		this.#base = new Set(this.#ids);
	}

	/**
	 * The hand has been lifted: whatever run was open is over. Only the direction is forgotten, so a
	 * later shift-click measures from where the sweep began; left behind, it would make the next
	 * shift-click unpick. Called from `TileGesture.#stopSweeping` on pointerup and pointercancel.
	 */
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

	/** Everything picked before this gesture, with the run from the anchor to here added to it,
	 *  or taken out of it, where the run began on a tile that was already picked. See `#run`.
	 *
	 * Recomputed from the anchor each time, so a range dragged one tile too far can be taken back.
	 * With no anchor, or one that has left the list, it is a plain pick.
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

	/**
	 * What a click on an already-picked grid means. One place, so every grid agrees.
	 *
	 * A plain click ADDS AND REMOVES, as a phone does once a long press has started selecting: with
	 * nothing picked a plain click opens instead and never arrives here, so "just this one" would
	 * only drop the set being built. `only` still starts a selection from nothing.
	 */
	pick(id: string, event: { shiftKey: boolean }, items: readonly string[]): void {
		if (event.shiftKey) {
			this.extendTo(id, items);
			return;
		}
		this.toggle(id);
	}

	/** Pick everything on screen, or drop it all if everything already is: one control, as a
	 *  select-all checkbox. */
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

	/**
	 * Pick every row a whole question matches, INCLUDING the ones the screen has not loaded.
	 *
	 * Not `toggleAll`, whose second press over an already-picked capped thousand would clear them.
	 * No anchor, since a button press is no place a shift-click could range from.
	 */
	pickWholeQuery(items: readonly string[]): void {
		this.#step();
		this.#ids = new Set(items);
		this.#anchor = null;
		this.#base = new Set(items);
		this.#beyondScreen = true;
	}

	/**
	 * Take up these ids as what is picked, as a fact rather than as a gesture: no step to take
	 * back, no run, and the anchor kept only while it is still among the rows given.
	 *
	 * For a selection that MIRRORS a pick held somewhere else (swap mode's picks, see
	 * `$lib/swap/sweep.svelte`): before each press it is told what the other store holds on screen,
	 * so a run measured from `#base` paints over the truth rather than over what it last saw.
	 */
	hold(ids: readonly string[]): void {
		this.#ids = new Set(ids);
		this.#base = new Set(ids);
		this.#beyondScreen = false;
		this.#run = null;
	}

	/** Forget anything that is no longer on screen, so an action never runs over ids the person
	 *  cannot see. */
	retain(items: readonly string[]): void {
		/*
		 * A whole-query pick is not a pick OF the screen, so the screen cannot say what has left it;
		 * cutting it here would collapse it to the page at the next re-read. A file deleted elsewhere
		 * stays until the pick ends, which every verb does as its write lands.
		 */
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
