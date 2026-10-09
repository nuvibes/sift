/*
 * Order or Shuffle for this sitting: Shuffle is one fixed order of the list (`Walk`), so Back
 * returns to the file just watched.
 */

import { mintSeed } from '$lib/grid/sort-state.svelte';
import { shuffled } from '$lib/player/shuffle';
import type { LoopMode } from './loop-modes';

export interface Step {
	id: string;
	runs: boolean;
}

/** `more` is null when what is held IS the whole list; otherwise the server shuffles it. */
export interface WalkSource {
	held: readonly Step[];
	more: {
		total: number;
		/**
		 * A block of the server's seeded shuffle (Browse's Random); null where it has none (Loops).
		 */
		shuffled: ((offset: number, limit: number, seed: number) => Promise<Step[]>) | null;
		inOrder: (offset: number, limit: number) => Promise<Step[]>;
	} | null;
}

/** About a page of the wall, so crossing one costs one request. */
const BLOCK = 60;

/**
 * One shuffled pass and where the run stands; Back means the file last landed on, never a skipped
 * still.
 */
export class Walk {
	/** -1 on the start file, otherwise a position in the order. */
	cursor = $state(-1);
	/**
	 * A file reached by Randomize or "Similar to this": Next carries on the walk, Back returns
	 * here.
	 */
	detour = $state<string | null>(null);

	readonly start: Step;
	readonly seed: number;

	#source: WalkSource;
	#order: Step[] = [];
	#ids = new Set<string>();
	#read = 0;
	#complete: boolean;
	#seen = new Set<number>([-1]);
	/** Stepped over, never removed, so no position moves. */
	#gone = new Set<string>();
	/** One step at a time, or two presses land on one file. */
	#queue: Promise<unknown> = Promise.resolve();

	constructor(start: Step, source: WalkSource, seed: number = mintSeed()) {
		this.start = start;
		this.seed = seed;
		this.#source = source;
		this.#ids.add(start.id);
		if (source.more === null) {
			this.#take(shuffled(source.held));
			this.#complete = true;
		} else {
			this.#complete = false;
		}
	}

	get current(): string {
		return this.#idAt(this.cursor);
	}

	holds(id: string): boolean {
		return this.#ids.has(id) || this.detour === id;
	}

	get goesOn(): boolean {
		return !this.#complete || this.#order.some((step) => !this.#gone.has(step.id));
	}

	canStepBack(id: string): boolean {
		if (this.detour !== null && this.detour === id) return this.#usable(this.cursor);
		return this.#before(this.#positionOf(id) ?? this.cursor) !== null;
	}

	/** From a detour, the file it was taken from; null at the start. */
	previous(id: string): string | null {
		if (this.detour !== null && this.detour === id) {
			this.detour = null;
			return this.#usable(this.cursor) ? this.current : null;
		}
		this.#standOn(id);
		const to = this.#before(this.cursor);
		if (to === null) return null;
		this.cursor = to;
		return this.current;
	}

	/**
	 * The next file, reading more when needed; at the true end a new pass of the SAME order, or
	 * null under `wraps: false`. `pictures` says whether a still is somewhere to land.
	 */
	next(
		id: string,
		{ pictures, wraps = true }: { pictures: boolean; wraps?: boolean }
	): Promise<string | null> {
		const step = this.#queue.then(() => this.#next(id, pictures, wraps));
		this.#queue = step.catch(() => undefined);
		return step;
	}

	peek(
		id: string,
		{ pictures, wraps = true }: { pictures: boolean; wraps?: boolean }
	): Promise<string | null> {
		const step = this.#queue.then(async () => {
			const from = this.detour === id ? this.cursor : (this.#positionOf(id) ?? this.cursor);
			const to = await this.#after(from, pictures, wraps);
			return to === null ? null : this.#idAt(to.at);
		});
		this.#queue = step.catch(() => undefined);
		return step;
	}

	runsOf(id: string): boolean | null {
		if (id === this.start.id) return this.start.runs;
		return this.#order.find((step) => step.id === id)?.runs ?? null;
	}

	forget(id: string): void {
		this.#gone.add(id);
		if (this.detour === id) this.detour = null;
	}

	async #next(id: string, pictures: boolean, wraps: boolean): Promise<string | null> {
		this.#standOn(id);
		this.detour = null;
		const to = await this.#after(this.cursor, pictures, wraps);
		if (to === null) return null;
		if (to.wraps) this.#seen = new Set([to.at]);
		else this.#seen.add(to.at);
		this.cursor = to.at;
		return this.current;
	}

	/* Never onto the file just finished while anything else qualifies. Moves nothing. */
	async #after(
		from: number,
		pictures: boolean,
		wraps: boolean
	): Promise<{ at: number; wraps: boolean } | null> {
		for (let at = from + 1; ;) {
			for (; at < this.#order.length; at += 1) {
				if (this.#fits(this.#order[at], pictures)) return { at, wraps: false };
			}
			if (!(await this.#readMore())) break;
		}
		if (!wraps) return null;
		const finished = this.#idAt(from);
		const candidates = [-1, ...this.#order.keys()].filter((at) =>
			this.#fits(at < 0 ? this.start : this.#order[at], pictures)
		);
		const to = candidates.find((at) => this.#idAt(at) !== finished) ?? candidates[0];
		return to === undefined ? null : { at: to, wraps: true };
	}

	#fits(step: Step, pictures: boolean): boolean {
		return !this.#gone.has(step.id) && (pictures || step.runs);
	}

	#before(from: number): number | null {
		for (let at = from - 1; at >= -1; at -= 1) {
			if (this.#seen.has(at) && this.#usable(at)) return at;
		}
		return null;
	}

	#usable(at: number): boolean {
		return !this.#gone.has(this.#idAt(at));
	}

	#idAt(at: number): string {
		return at < 0 ? this.start.id : this.#order[at].id;
	}

	#positionOf(id: string): number | null {
		if (id === this.start.id) return -1;
		const at = this.#order.findIndex((step) => step.id === id);
		return at < 0 ? null : at;
	}

	/* The corner player handed a file that is in the walk but not where it stood. */
	#standOn(id: string): void {
		if (this.detour === id) return;
		const at = this.#positionOf(id);
		if (at === null || at === this.cursor) return;
		this.#seen.add(at);
		this.cursor = at;
	}

	#take(steps: readonly Step[]): void {
		for (const step of steps) {
			if (this.#ids.has(step.id)) continue;
			this.#ids.add(step.id);
			this.#order.push(step);
		}
	}

	async #readMore(): Promise<boolean> {
		const more = this.#source.more;
		if (this.#complete || more === null) return false;
		if (more.shuffled === null) return this.#readWhole(more.total, more.inOrder);
		let block: Step[] = [];
		try {
			block = await more.shuffled(this.#read, BLOCK, this.seed);
		} catch {
			// A run goes on with what it has.
			block = [];
		}
		this.#read += block.length;
		this.#take(block);
		if (block.length === 0 || this.#read >= more.total) this.#complete = true;
		return block.length > 0;
	}

	/*
	 * Only the wall of Loops, read whole and shuffled here: a block at a time would not be a
	 * shuffle.
	 */
	async #readWhole(
		total: number,
		inOrder: (offset: number, limit: number) => Promise<Step[]>
	): Promise<boolean> {
		const everything: Step[] = [...this.#source.held];
		while (this.#read < total) {
			let block: Step[] = [];
			try {
				block = await inOrder(this.#read, BLOCK);
			} catch {
				block = [];
			}
			if (block.length === 0) break;
			this.#read += block.length;
			everything.push(...block);
		}
		this.#complete = true;
		const before = this.#order.length;
		this.#take(shuffled(everything));
		return this.#order.length > before;
	}
}

class Run {
	shuffle = $state(false);

	/** `$state.raw`: replaced, never edited from outside. */
	walk = $state.raw<Walk | null>(null);

	/**
	 * Stop at the end under Shuffle stops at the end of the SHUFFLED LIST (`Walk.next`'s `wraps`).
	 */
	movesOnAfter(mode: LoopMode): boolean {
		return mode === 'loop_all' || (mode === 'once' && this.shuffle);
	}

	toggle(): void {
		this.shuffle = !this.shuffle;
		this.walk = null;
	}

	/** The pulse of a list that is not itself reactive, so Next and Back are asked again. */
	relisted = $state(0);

	moved(): void {
		this.relisted += 1;
	}

	reset(): void {
		this.walk = null;
	}
}

export const run = new Run();
