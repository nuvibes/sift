/* Whether a run plays in order or at random, and, when it is at random, WHICH order.
 *
 * Held for this sitting and written down nowhere. It is the same call the A-B loop makes and for
 * the same reason: a shuffle set once and forgotten looks like a player that has lost track of
 * where it is, and there would be nothing on screen to say otherwise days later.
 *
 * What it shuffles is what you opened the file from (the search, the collection, the folder)
 * rather than the whole library. Leaving what you chose to look at is what the randomize control
 * does, deliberately and one press at a time.
 *
 * ## Shuffle is ONE FIXED ORDER, not a draw per press
 *
 * A fresh random draw on every Next (a block at a random offset of a long list, `Math.random`
 * with no memory on a short one) would send Back to the file beside this one in the LIST rather
 * than to the one just watched (or nowhere, when the draw had come from off the page), bring
 * files round again while others never came, and leave the corner player out of it.
 *
 * A shuffled playlist is what everybody already knows, so that is what this is: the list is put
 * in one random order when the first step is taken, and Next and Back walk it. `Walk` below holds
 * it. The file that was open when the walk began is where it starts (position -1), so Back from
 * the first shuffled file returns to it; nothing comes round again until everything has played;
 * and both players (the full-size one and the corner) ask the one walk, through `asset-view`.
 */

import { mintSeed } from '$lib/grid/sort-state.svelte';
import { shuffled } from '$lib/player/shuffle';
import type { LoopMode } from './loop-modes';

/** One file in the list a run walks, and whether it plays on its own (a video or GIF, not a still). */
export interface Step {
	id: string;
	runs: boolean;
}

/**
 * Where a walk reads the list it shuffles. Handed in by `asset-view`, which is what holds the list.
 *
 * `more` is null when what is held IS the whole list (a short wall, a face pile, a list that
 * cannot be read any further), and then the walk shuffles what it holds, here, with the shared
 * shuffle. Otherwise the list is longer than what the client holds, and reading all of it to
 * shuffle it would be a page load per file; the server shuffles it instead.
 */
export interface WalkSource {
	held: readonly Step[];
	more: {
		/** How many there are in the whole list. */
		total: number;
		/**
		 * A block of the SAME list in the server's seeded shuffle: the permutation Browse's own
		 * Random order walks, so every block of one seed is a page of one arrangement and nothing is
		 * drawn twice. Null where the list's server has no shuffle to offer (the wall of Loops).
		 */
		shuffled: ((offset: number, limit: number, seed: number) => Promise<Step[]>) | null;
		/** A block in the list's own order, for a list the server cannot shuffle. */
		inOrder: (offset: number, limit: number) => Promise<Step[]>;
	} | null;
}

/** How many to ask for at a time: a page of the wall, near enough, so crossing one costs one request. */
const BLOCK = 60;

/**
 * One shuffled pass over one list, and where in it the run stands.
 *
 * `cursor` is a position in `order`, which never holds the start file: -1 IS the start file. A
 * position is added to `seen` when the run lands on it, which is what makes Back mean "the file I
 * just watched" rather than "the position before this one": a run that plays through steps OVER
 * stills, and Back must not land on a photograph nobody was shown.
 */
export class Walk {
	/** Where the walk stands: -1 on the start file, otherwise a position in the order. */
	cursor = $state(-1);
	/**
	 * A file on screen that is NOT in this walk: one reached by Randomize or "Similar to this".
	 * Next carries on from where the walk stands; Back comes back to it.
	 */
	detour = $state<string | null>(null);

	readonly start: Step;
	readonly seed: number;

	#source: WalkSource;
	#order: Step[] = [];
	/** Every id in the walk, the start file's included, which is what keeps a file from coming twice. */
	#ids = new Set<string>();
	/** How far through the server's arrangement has been read. */
	#read = 0;
	/** Whether the whole order is known. */
	#complete: boolean;
	#seen = new Set<number>([-1]);
	/** Files deleted while the walk was open. Stepped over, never removed, so no position moves. */
	#gone = new Set<string>();
	/** One step at a time: two presses racing would both read the same cursor and land on one file. */
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

	/** The file the walk stands on. */
	get current(): string {
		return this.#idAt(this.cursor);
	}

	/** Whether this file is part of the walk, or the detour it is on. */
	holds(id: string): boolean {
		return this.#ids.has(id) || this.detour === id;
	}

	/** Whether there is anything else to step on to, without asking the server. */
	get goesOn(): boolean {
		return !this.#complete || this.#order.some((step) => !this.#gone.has(step.id));
	}

	/** Whether Back has somewhere to go from the file shown. */
	canStepBack(id: string): boolean {
		if (this.detour !== null && this.detour === id) return this.#usable(this.cursor);
		return this.#before(this.#positionOf(id) ?? this.cursor) !== null;
	}

	/**
	 * Back: the file watched before this one in the walk, or, from a detour, the file the detour
	 * was taken from. Null at the start of the walk.
	 */
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
	 * Next: the next file in the order, reading more of it from the server when the walk reaches
	 * the end of what it holds. At the true end a new pass begins over the SAME order, from the
	 * start file: a shuffled playlist on repeat, which is what "play through" means in order too.
	 * `wraps: false` is Stop at the end under Shuffle: at the true end there is nowhere to go, and
	 * the answer is null.
	 *
	 * `pictures` says whether a still is somewhere to land. A person pressing Next means the next
	 * one whatever it is; a run moving on by itself steps over a photograph unless the account asked
	 * for photographs to be held. Null only when nothing in the whole list qualifies.
	 */
	next(
		id: string,
		{ pictures, wraps = true }: { pictures: boolean; wraps?: boolean }
	): Promise<string | null> {
		const step = this.#queue.then(() => this.#next(id, pictures, wraps));
		this.#queue = step.catch(() => undefined);
		return step;
	}

	/** Whether a file in this walk plays on its own, or null when the walk does not hold it. */
	runsOf(id: string): boolean | null {
		if (id === this.start.id) return this.start.runs;
		return this.#order.find((step) => step.id === id)?.runs ?? null;
	}

	/** A file was deleted. It is stepped over from now on; Back never lands on it. */
	forget(id: string): void {
		this.#gone.add(id);
		if (this.detour === id) this.detour = null;
	}

	async #next(id: string, pictures: boolean, wraps: boolean): Promise<string | null> {
		this.#standOn(id);
		this.detour = null;
		let at = this.cursor + 1;
		for (;;) {
			for (; at < this.#order.length; at += 1) {
				if (this.#fits(this.#order[at], pictures)) return this.#land(at);
			}
			if (!(await this.#readMore())) break;
		}
		return wraps ? this.#wrap(pictures) : null;
	}

	/* The end of the order. A new pass, over the same order, from the start file, and never onto
	   the file just finished while anything else qualifies, which would be repeating it. */
	#wrap(pictures: boolean): string | null {
		const finished = this.current;
		const candidates = [-1, ...this.#order.keys()].filter((at) =>
			this.#fits(at < 0 ? this.start : this.#order[at], pictures)
		);
		const to = candidates.find((at) => this.#idAt(at) !== finished) ?? candidates[0];
		if (to === undefined) return null;
		this.#seen = new Set([to]);
		this.cursor = to;
		return this.current;
	}

	#land(at: number): string {
		this.#seen.add(at);
		this.cursor = at;
		return this.current;
	}

	#fits(step: Step, pictures: boolean): boolean {
		return !this.#gone.has(step.id) && (pictures || step.runs);
	}

	/* The nearest position before `from` that was actually landed on, and still exists. */
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

	/* Put the walk where the file on screen is, when they disagree: the corner player handed a
	   file that is in the walk but not where it last stood. A detour stays a detour. */
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

	/* More of the order. False once there is no more to read. */
	async #readMore(): Promise<boolean> {
		const more = this.#source.more;
		if (this.#complete || more === null) return false;
		if (more.shuffled === null) return this.#readWhole(more.total, more.inOrder);
		let block: Step[] = [];
		try {
			block = await more.shuffled(this.#read, BLOCK, this.seed);
		} catch {
			// A run is not worth an error message. It goes on with what it has.
			block = [];
		}
		this.#read += block.length;
		this.#take(block);
		if (block.length === 0 || this.#read >= more.total) this.#complete = true;
		return block.length > 0;
	}

	/*
	 * A list the server cannot shuffle, read whole and shuffled here.
	 *
	 * Only the wall of Loops is this today, and it is bounded by how many stretches somebody has
	 * marked by hand. Shuffling it a block at a time would not be a shuffle (the first block would
	 * always be the newest marks), so it is read once, at the first step past what is held.
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
	/** Whether the run walks the list in one shuffled order rather than in the list's own. */
	shuffle = $state(false);

	/**
	 * The shuffled order being walked, once a step has been taken with Shuffle on. `$state.raw`:
	 * the walk is replaced, never edited from outside, and its own fields carry what moves.
	 */
	walk = $state.raw<Walk | null>(null);

	/**
	 * Whether a file that finishes by itself moves the run on, under the repeat answer `mode`.
	 *
	 * Play through always does. Stop at the end does under Shuffle, where the end it stops at is
	 * the end of the SHUFFLED LIST rather than of the file: the run
	 * walks the shuffled order once and stops where it runs out (`Walk.next`'s `wraps`, false
	 * under Stop at the end). Without Shuffle it stops at the file's end. Repeat this plays the
	 * same file again and is the player's own.
	 */
	movesOnAfter(mode: LoopMode): boolean {
		return mode === 'loop_all' || (mode === 'once' && this.shuffle);
	}

	toggle(): void {
		this.shuffle = !this.shuffle;
		// Either way the old order is finished with. Turning Shuffle on starts a fresh one from the
		// file on screen at the first step; turning it off goes back to the list's own order.
		this.walk = null;
	}

	/**
	 * Counts each time the list behind the panel moved under the file on screen, so whatever
	 * draws Next and Back from it asks again. The list itself is not reactive; this is its pulse.
	 */
	relisted = $state(0);

	/** The list behind the panel changed. See `relisted`. */
	moved(): void {
		this.relisted += 1;
	}

	/** A new list was opened: whatever order was being walked was an order of a different list. */
	reset(): void {
		this.walk = null;
	}
}

export const run = new Run();
