// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The order a wall of entities is in, kept somewhere the screen cannot take with it.
 *
 * Opening a row RUNS that row's route, so the wall behind it is torn down and built again on the
 * way back. An order held in the screen goes with it, and the wall returns in an order nobody
 * chose, with nothing on it saying why: picking `Fewest files`, opening a set and coming back to
 * `Most files` again.
 *
 * Written once here rather than three times beside the three walls. The rule has three edges in it
 * and each is quiet when it is wrong: an unknown value must read as the default rather than as an
 * error, storage that refuses must leave the wall sorting anyway, and the value has to be read at
 * IMPORT so the first paint is already in the chosen order. Three copies of that is three chances
 * for one of them to be a little different, and the difference would be invisible until somebody
 * compared two walls.
 *
 * Remembered in this browser rather than on the account, for the reason the grid's own order is
 * (`lib/grid/sort-state`): it is a display arrangement the browser can answer before the first
 * paint, where a value arriving from the server a moment after the screen does would draw the wall
 * once and redraw it a beat later.
 *
 * Each wall keeps its OWN key. One key across the walls would let a choice made on Tags arrive on
 * Photo Sets as an order that wall does not offer. And the two lists genuinely differ.
 */

import { readStored, writeStored } from '$lib/shell/remembered.svelte';

export class WallSort {
	#key: string;
	#fallback: string;
	/** The orders this wall offers. Anything else is refused rather than sent. */
	#known: ReadonlySet<string>;

	/** The chosen order. Read it; write through `set`. */
	value = $state('');

	constructor(key: string, fallback: string, known: Iterable<string>) {
		this.#key = key;
		this.#fallback = fallback;
		this.#known = new Set(known);
		const stored = readStored(key);
		// A stale key (an older build's, or a hand-edited store) reads as the default. The server
		// refuses an order it does not know regardless, so a stale key cannot mis-sort a page.
		this.value = stored !== null && this.#known.has(stored) ? stored : fallback;
	}

	/** What this wall falls back to when nobody has chosen. */
	get fallback(): string {
		return this.#fallback;
	}

	set(next: string): void {
		if (!this.#known.has(next)) return;
		this.value = next;
		// Not remembered where storage refuses: `writeStored` swallows that. The wall still sorts
		// and forgets by the next visit, which is better than refusing to change order at all.
		writeStored(this.#key, next);
	}
}
