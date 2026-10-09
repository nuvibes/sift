/* The two long-running things this feature does, followed from outside any one screen. */

import { ModelFetchWatch } from '$lib/jobs/model-fetch';
import {
	FETCHING_MODELS,
	semanticAvailable,
	semanticCoverage,
	semanticStatus,
	type SemanticStatus
} from '$lib/search/semantic.svelte';

/** How often a run is asked how far it has got. The same pace the recognition pane uses: fast
 *  enough to look live, slow enough not to be a second load on a machine that is busy. */
const WATCH_EVERY = 2000;

/** How many readings the estimate is worked out over, and the fewest seconds it needs before it
 *  will say anything. Twenty readings at two seconds is about forty seconds of history. */
const MOST_WATCHED = 20;
const LEAST_WATCHED = 10;

/* Fetching the models: the model download watcher (`model-fetch`), with the sentence when they
 * arrive and the switch in the search box that turns on when they do. */
class ModelFetch extends ModelFetchWatch {
	constructor() {
		super(FETCHING_MODELS, async () => {
			const state = await semanticStatus().catch(() => null);
			void availability.refresh();
			return state?.ready
				? 'The models are on this machine. Sift can search by meaning now.'
				: null;
		});
	}
}

class Describing {
	/** The status, re-read while there is work left. Null until the first read lands. */
	status = $state<SemanticStatus | null>(null);
	outcome = $state<string | null>(null);

	#generation = 0;

	/* Whether anything is actually being done right now. */
	get running(): boolean {
		if (this.status === null || !this.status.ready) return false;
		return this.status.running_jobs > 0;
	}

	/** Work left, and nothing doing it. The state a run that was interrupted leaves behind, and
	 *  the one a screen must not draw as progress. */
	get stopped(): boolean {
		const state = this.status;
		if (state === null) return false;
		return state.ready && state.waiting_files > 0 && state.running_jobs === 0;
	}

	get done(): number {
		return this.status?.described_files ?? 0;
	}

	get left(): number {
		return this.status?.waiting_files ?? 0;
	}

	/* What the count has done lately, for working out how long the rest will take. */
	#seen = $state<{ at: number; left: number }[]>([]);

	/* Roughly how long is left, in seconds, or null when there is nothing honest to say. */
	get remaining(): number | null {
		const first = this.#seen[0];
		const last = this.#seen[this.#seen.length - 1];
		if (!first || !last || this.left <= 0) return null;
		const seconds = (last.at - first.at) / 1000;
		const gotThrough = first.left - last.left;
		if (seconds < LEAST_WATCHED || gotThrough <= 0) return null;
		return Math.round(this.left / (gotThrough / seconds));
	}

	/** How far through, as a fraction. Null when there is nothing to divide by: a fresh install
	 *  with nothing described and nothing waiting is not "0% done", it is not started. */
	get fraction(): number | null {
		const total = this.done + this.left;
		if (total <= 0) return null;
		return this.done / total;
	}

	/** Read once, and keep reading for as long as there is work. */
	async attach(): Promise<void> {
		this.#generation += 1;
		await this.#poll(this.#generation, { immediately: true, asked: false });
	}

	/** An Identify run with Meaning was just asked for here: read afresh, and keep reading. */
	follow(): void {
		this.outcome = null;
		this.#seen = [];
		this.#generation += 1;
		void this.#poll(this.#generation, { immediately: false, asked: true });
	}

	/** Stop reading. The counts stay on screen; only the polling ends. */
	detach(): void {
		this.#generation += 1;
	}

	async #poll(
		mine: number,
		{ immediately, asked }: { immediately: boolean; asked: boolean }
	): Promise<void> {
		let first = immediately;
		// Only a watch that asked for describing, or saw it under way, can see it stop.
		let under = asked;
		for (;;) {
			if (!first) await new Promise((resume) => setTimeout(resume, WATCH_EVERY));
			first = false;
			if (mine !== this.#generation) return;
			const state = await semanticStatus().catch(() => null);
			if (mine !== this.#generation) return;
			if (state === null) return;
			this.status = state;
			this.#seen = [...this.#seen, { at: Date.now(), left: state.waiting_files }].slice(
				-MOST_WATCHED
			);
			if (!state.ready) {
				// Switched off, or the models went. Every job stops on its own (each checks the
				// switch first), so there is nothing to cancel, only a screen to stop lying.
				if (under) this.outcome = 'Stopped. Nothing described so far has been lost.';
				return;
			}
			under ||= state.running_jobs > 0;
			if (state.running_jobs > 0) continue;
			if (state.waiting_files > 0) {
				// Nothing running and work left. Stopped, not finished, and said so plainly,
				// because the alternative is a bar that never moves again and no way to tell why.
				this.outcome =
					`Stopped with ${state.waiting_files} still to do. ` +
					'Run now on Identify, under Import tasks, picks up where it left off.';
				return;
			}
			if (this.outcome === null && this.done > 0) {
				this.outcome = 'Everything Sift can see has been described.';
			}
			return;
		}
	}
}

/* Whether the search box should offer to search by meaning. */
class Availability {
	available = $state(false);
	#asked = false;

	/** Read it, once per page load. */
	async load(): Promise<void> {
		if (this.#asked) return;
		this.#asked = true;
		await this.refresh();
	}

	/** Read it again, now. Called by whatever just changed the answer. */
	async refresh(): Promise<void> {
		try {
			this.available = (await semanticAvailable()).available;
		} catch {
			// Nobody signed in yet, or the install cannot answer. Either way: do not offer it.
			this.available = false;
		}
	}
}

/* How much of the library a search by meaning can currently reach. */
class Coverage {
	described = $state(0);
	library = $state(0);
	#asking: Promise<void> | null = null;

	/** Read it. Concurrent asks share one request: a wall redrawing does not need two. */
	async load(): Promise<void> {
		if (this.#asking) return this.#asking;
		this.#asking = this.#read();
		try {
			await this.#asking;
		} finally {
			this.#asking = null;
		}
	}

	async #read(): Promise<void> {
		try {
			const answer = await semanticCoverage();
			this.described = answer.described;
			this.library = answer.library;
		} catch {
			// Nobody signed in yet, or the install cannot answer.
			this.described = 0;
			this.library = 0;
		}
	}
}

export const modelFetch = new ModelFetch();
export const describing = new Describing();
export const availability = new Availability();
export const coverage = new Coverage();
