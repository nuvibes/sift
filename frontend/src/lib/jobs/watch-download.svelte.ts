/* Following one long download from a screen that may be closed and reopened while it runs. */

import { accountTurn } from '$lib/shell/account-scoped';
import { api } from '$lib/api/client';
import { isFinished } from '$lib/jobs/queue.svelte';
import type { components } from '$lib/api/schema';

/** How often a run is asked how far it has got. Fast enough to look live, slow enough not to be a
 *  second load on a machine that is already busy. */
const WATCH_EVERY = 2000;

type JobRow = Pick<
	components['schemas']['JobView'],
	'id' | 'state' | 'progress' | 'note' | 'position'
>;

/** One job's progress, or null when the queue does not list it any more. */
export async function jobProgress(
	type: string,
	jobId: string
): Promise<{
	state: string;
	progress: number;
	note: string | null;
	position: number | null;
} | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type, limit: 50 }
	});
	const job = page.jobs.find((one) => one.id === jobId);
	return job
		? {
				state: job.state,
				progress: job.progress,
				note: job.note ?? null,
				position: job.position ?? null
			}
		: null;
}

/** A job of this kind that has not ended, with the state the queue last gave it, or null. */
/* Not exported: the watch in this file is the only caller. */
async function jobToFollow(type: string): Promise<{ id: string; state: string } | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type, limit: 50 }
	});
	const job = page.jobs.find((one) => !isFinished(one.state));
	return job ? { id: job.id, state: job.state } : null;
}

/** The states a job is in before anything has picked it up. */
const WAITING_STATES: readonly string[] = ['queued', 'blocked'];

/** The wait, with its place in the line where the server gave one. */
export function sayWaiting(position: number | null): string {
	if (position === null) return 'Waiting to start';
	if (position === 1) return 'Next to start';
	const ahead = position - 1;
	return `Waiting to start — ${ahead.toLocaleString()} ahead`;
}

export class DownloadWatch {
	/** The job being followed, or null when nothing is running. */
	jobId = $state<string | null>(null);
	fraction = $state(0);
	/** What is being fetched, in words, from the job's own note. */
	note = $state<string | null>(null);
	/** What happened, kept after the run ends. */
	outcome = $state<string | null>(null);
	/** The state the queue last gave the job being followed, or null when nothing is followed. */
	state = $state<string | null>(null);

	/* Which watcher is the live one. */
	#generation = 0;

	constructor(
		/** The job kind, as the queue knows it. */
		private readonly type: string,
		/** What to say and do when it ends, however it ended. */
		private readonly finished: () => Promise<string>,
		/** What to say when following it became impossible. */
		private readonly lost = "Couldn't follow the download. Open Activity to see it."
	) {}

	/** Whether there is a download to draw a bar for. */
	get running(): boolean {
		return this.jobId !== null;
	}

	/** Where it is in the line, or null while nothing is being followed or nothing said. */
	position = $state<number | null>(null);

	/** Whether it has not been picked up yet, so the fraction means nothing. */
	get waiting(): boolean {
		return this.jobId !== null && WAITING_STATES.includes(this.state ?? '');
	}

	/** Where the download has got to, in the words a screen puts beside the bar. */
	get status(): string {
		if (this.waiting) return sayWaiting(this.position);
		const percent = Math.round(this.fraction * 100);
		const what = this.note ?? (this.fraction >= 1 ? 'finishing up' : 'downloading');
		return `${percent}% — ${what}`;
	}

	/** Follow a run just started here. */
	follow(jobId: string, state = 'queued'): void {
		this.jobId = jobId;
		this.state = state;
		this.fraction = 0;
		this.note = null;
		this.position = null;
		this.outcome = null;
		this.#generation += 1;
		void this.#watch(this.#generation, jobId);
	}

	/** Pick up a run already going. Safe to call on every mount: one already being followed is left
	 *  alone, so several screens opening does not mean several watchers. */
	async resume(): Promise<void> {
		if (this.jobId) return;
		const found = await jobToFollow(this.type).catch(() => null);
		// Checked again: the read above is a round trip, and a run started while it was in flight
		// is the one to follow.
		if (found && !this.jobId) this.follow(found.id, found.state);
	}

	/** Something outside failed to even start it. */
	couldNotStart(why: string): void {
		this.#generation += 1;
		this.jobId = null;
		this.state = null;
		this.outcome = why;
	}

	async #watch(mine: number, jobId: string): Promise<void> {
		const turn = accountTurn();
		for (;;) {
			await new Promise((resume) => setTimeout(resume, WATCH_EVERY));
			if (mine !== this.#generation) return;
			// The session ended: let go, so the next one picks the job up again (`resume`).
			if (turn !== accountTurn()) {
				this.jobId = null;
				this.state = null;
				return;
			}
			let job = null;
			try {
				job = await jobProgress(this.type, jobId);
			} catch {
				this.jobId = null;
				this.state = null;
				this.outcome = this.lost;
				return;
			}
			if (mine !== this.#generation) return;
			if (!job || isFinished(job.state)) {
				this.jobId = null;
				this.state = null;
				this.outcome = await this.finished();
				return;
			}
			this.state = job.state;
			this.fraction = job.progress;
			this.note = job.note;
			this.position = job.position;
		}
	}
}
