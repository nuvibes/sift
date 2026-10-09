/*
 * The A-B loop, held here rather than on a player: a clip handed to the corner panel builds a new
 * one. Written down nowhere (a forgotten loop would look like a fault), cleared on another clip,
 * and ended when the last player showing the clip goes. `abLoop` is shared by the players; a view
 * that shows the same clip separately builds its own `Loop`.
 */

export class Loop {
	owner = $state<string | null>(null);

	a = $state<number | null>(null);

	/** Null while only the start is marked. */
	b = $state<number | null>(null);

	/** Every player asks first, so a loop marked on one clip is not enforced on another. */
	owns(id: string): boolean {
		return this.owner === id;
	}

	get running(): boolean {
		return this.a !== null && this.b !== null && this.b > this.a;
	}

	get nextAction(): string {
		if (this.a === null) return 'Set the loop start';
		return this.b === null ? 'Set the loop end' : 'Clear the loop';
	}

	/** Marking claims the loop, dropping marks on whatever held it before. */
	mark(id: string, at: number): void {
		if (this.owner !== id) {
			this.owner = id;
			this.a = at;
			this.b = null;
			return;
		}
		if (this.a === null) {
			this.a = at;
			return;
		}
		if (this.b === null) {
			// A second press before the first point corrects it.
			if (at <= this.a) this.a = at;
			else this.b = at;
			return;
		}
		this.a = null;
		this.b = null;
	}

	/**
	 * Both ends together, from a saved loop, so opening one plays the loop. Still not persistent.
	 * Refused when the end is not after the start, which would seek backwards forever.
	 */
	arm(id: string, from: number, to: number): void {
		if (!(to > from)) return;
		this.owner = id;
		this.a = from;
		this.b = to;
	}

	/** Without letting it cross the other: such a loop never repeats. */
	moveTo(which: 'a' | 'b', at: number): void {
		if (which === 'a') this.a = this.b === null ? at : Math.min(at, this.b);
		else this.b = this.a === null ? at : Math.max(at, this.a);
	}

	/** Counted: a handover builds the second player before or after the first goes. */
	watch(id: string): void {
		this.#watchers.set(id, (this.#watchers.get(id) ?? 0) + 1);
	}

	/**
	 * Marks drop only when the LAST player on the clip has gone, checked a turn later: in a
	 * handover the count can touch zero for an instant.
	 */
	unwatch(id: string): void {
		const left = (this.#watchers.get(id) ?? 0) - 1;
		if (left > 0) this.#watchers.set(id, left);
		else this.#watchers.delete(id);
		if (this.owner === null) return;
		if (this.#pending !== null) clearTimeout(this.#pending);
		this.#pending = setTimeout(() => {
			this.#pending = null;
			if (this.owner !== null && !this.#watchers.has(this.owner)) this.clear();
		}, 0);
	}

	clear(): void {
		if (this.#pending !== null) {
			clearTimeout(this.#pending);
			this.#pending = null;
		}
		this.owner = null;
		this.a = null;
		this.b = null;
	}

	reset(): void {
		this.clear();
		this.#watchers.clear();
	}

	#watchers = new Map<string, number>();

	#pending: ReturnType<typeof setTimeout> | null = null;
}

export const abLoop = new Loop();
