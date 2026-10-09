// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The order of a wall of entities, kept where the screen cannot take it with it: opening a row
 * tears the wall down. In this browser, read at import so the first paint is in order; one key per
 * wall, since the walls offer different orders.
 */

import { readStored, writeStored } from '$lib/shell/remembered.svelte';

export class WallSort {
	#key: string;
	#fallback: string;
	#known: ReadonlySet<string>;

	value = $state('');

	constructor(key: string, fallback: string, known: Iterable<string>) {
		this.#key = key;
		this.#fallback = fallback;
		this.#known = new Set(known);
		const stored = readStored(key);
		// A stale key reads as the default; the server refuses an unknown order anyway.
		this.value = stored !== null && this.#known.has(stored) ? stored : fallback;
	}

	get fallback(): string {
		return this.#fallback;
	}

	set(next: string): void {
		if (!this.#known.has(next)) return;
		this.value = next;
		// Where storage refuses, the wall still sorts and forgets by the next visit.
		writeStored(this.#key, next);
	}
}
