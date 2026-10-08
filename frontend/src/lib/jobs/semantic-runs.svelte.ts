/*
 * The two long-running things this feature does, followed from outside any one screen.
 *
 * Held at module level for the reason recognition's sweep is: a run outlives the pane that started
 * it. Kept inside the component, closing the settings sheet would throw the watcher away, and
 * coming back would show a button, so the only way to find out whether several hundred megabytes
 * had arrived would be to reload the page. Here it goes on running, and reopening the pane joins
 * whatever is already happening rather than starting from a blank screen.
 *
 * The two are followed in completely different ways, and that is deliberate.
 *
 * **Fetching the models is a job**, so it is followed as one: a job id, a fraction, and an outcome
 * when the queue stops listing it.
 *
 * **Describing the library is NOT.** It is Identify's Meaning product, started from the Identify
 * row on Importing or from this pane, and its tasks are a crowd of per-file jobs on the Activity
 * screen. Following one job would say nothing about the rest. So the progress here is the thing
 * itself: how many files have been described, against how many are still to do, read from the
 * status the server already computes. It needs no job id, which is also what makes it resume for
 * free: a page opened an hour later reads the same two numbers and shows the same bar.
 */

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

	/* Whether anything is actually being done right now.
	 *
	 * **Asked of the QUEUE, not of the file counts.** Files still to do is not the same question: a run stopped halfway
	 * leaves exactly as many files undone as a run still going, so a bar drawn from that number
	 * sits at whatever it reached, for ever, describing work that will never happen.
	 *
	 * Turning the switch off mid-run does not cancel the queued work:
	 * every one of those jobs runs, finds the feature off, and finishes having done nothing. The
	 * queue empties, the files stay undescribed, and switching back on does not bring them back.
	 * What recovers it is an Identify run with Meaning, and
	 * `stopped` below is what tells the screen to offer that instead of a bar.
	 */
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

	/* What the count has done lately, for working out how long the rest will take.
	 *
	 * A window rather than the whole run, and that is not a refinement: files are wildly unequal
	 * (a photograph is one moment and a feature video is sixty), so an average taken over everything
	 * since the start keeps reporting a rate the machine has not managed for some time, and drifts
	 * further from the truth the longer it goes on. What somebody wants is how long the REST will
	 * take at the rate it is going NOW.
	 */
	#seen = $state<{ at: number; left: number }[]>([]);

	/* Roughly how long is left, in seconds, or null when there is nothing honest to say.
	 *
	 * Null until there is enough to divide by, and null again if the count has not moved across the
	 * whole window, which is what a stalled run looks like. No number is better than one that ticks
	 * up by a second every second.
	 */
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

	/** Read once, and keep reading for as long as there is work. Called on mount, so opening the
	 *  pane halfway through a run shows the run. */
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
				// Nothing running and work left. Stopped, not finished, and said so plainly, because
				// the alternative is a bar that never moves again and no way to tell why.
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

/*
 * Whether the search box should offer to search by meaning.
 *
 * Held here rather than asked for by the box, so that turning the switch off makes the control go
 * immediately instead of at the next full page load. Asking once when the box is drawn is correct
 * for a fact about the machine and wrong for one somebody can change from another screen, and this
 * is both.
 */
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

/*
 * How much of the library a search by meaning can currently reach.
 *
 * Held here beside the availability above, and read for a different reason: that one decides
 * whether a control appears, this one explains an answer. A search by meaning can only answer out
 * of what has been described, and until the background pass has been round the library that is a
 * minority of it. So a thin set of results for a good phrase reads as the files not being there.
 *
 * Asked by the wall that draws such a result set, and only when it draws one. It is one count over
 * the library on the server, so it is not something to fold into the read every client makes on
 * every page load: most of them never search by meaning at all.
 *
 * Read again each time a wall starts drawing a search by meaning, rather than once per page load
 * like the availability above. The background pass moves this number while somebody is sitting
 * here, and a sentence that froze at whatever it said when the tab was opened would be wrong in
 * the direction that matters. It would keep saying a library is less described than it is.
 */
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
			// Nobody signed in yet, or the install cannot answer. Zero, which is how the sentence
			// says nothing at all rather than guessing at a fraction.
			this.described = 0;
			this.library = 0;
		}
	}
}

export const modelFetch = new ModelFetch();
export const describing = new Describing();
export const availability = new Availability();
export const coverage = new Coverage();
