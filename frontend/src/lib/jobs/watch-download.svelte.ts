/*
 * Following one long download from a screen that may be closed and reopened while it runs.
 *
 * Shared by the model download and the graphics-card runtime: both fetch hundreds of megabytes
 * through the job queue, and none of this is about models:
 *
 * **It is held at module level by whoever uses it**, because a download outlives the pane that
 * started it. Kept inside a component, closing the settings sheet would throw the watcher away and
 * coming back would show a button, so the only way to find out whether the download had finished
 * would be to reload the page.
 *
 * **It can join one already running**, which is the same problem from the other side: a screen
 * opened while a download is in flight has to show the bar, not the button.
 *
 * **It keeps the outcome after the bar goes.** A bar that simply vanishes says nothing about
 * whether it finished or gave up, and those need different actions from whoever is reading.
 *
 * What is NOT here is what the download was for. The sentence at the end and any refreshing that
 * has to happen when it lands are the caller's, supplied as `finished`.
 */

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

/**
 * A job of this kind that has not ended, with the state the queue last gave it, or null.
 *
 * Not `runningJob`: picking up a job that has not ended is right (a download waiting its turn is
 * one to follow, not one to ignore, or the screen offers the button again and somebody starts a
 * second), but the word "running" travels. `DownloadWatch.running` read as "it is downloading"
 * would draw "0% - downloading" over a job sitting minutes behind hundreds of others. So the state
 * comes back with the id and the screens are told which of the two it is.
 */
/* Not exported: the watch in this file is the only caller. */
async function jobToFollow(type: string): Promise<{ id: string; state: string } | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type, limit: 50 }
	});
	const job = page.jobs.find((one) => !isFinished(one.state));
	return job ? { id: job.id, state: job.state } : null;
}

/** The states a job is in before anything has picked it up. `blocked` is waiting too: it is
 *  waiting on something rather than on a free worker, and neither is progress. */
const WAITING_STATES: readonly string[] = ['queued', 'blocked'];

/**
 * The wait, with its place in the line where the server gave one.
 *
 * Exported so the words are testable on their own and written once for every screen that draws a
 * waiting job. Null is not a failure (a `blocked` job is waiting on something rather than on a
 * free worker, and it has no place in the line at all), so the plain sentence is the fallback and
 * not an error state.
 */
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
	/** What is being fetched, in words, from the job's own note. A set of several files of very
	 *  different sizes means a percentage alone says nothing about what is happening. */
	note = $state<string | null>(null);
	/** What happened, kept after the run ends. */
	outcome = $state<string | null>(null);
	/** The state the queue last gave the job being followed, or null when nothing is followed. A
	 *  download starts life waiting for a worker, and on a busy machine that is where it spends
	 *  most of its life. */
	state = $state<string | null>(null);

	/* Which watcher is the live one. A counter rather than a flag: a watcher spends nearly all its
	   life asleep, so a flag cleared on waking is still set for up to a tick after the run it
	   followed has gone, and a second run started inside that tick gets no watcher at all. */
	#generation = 0;

	constructor(
		/** The job kind, as the queue knows it. */
		private readonly type: string,
		/** What to say and do when it ends, however it ended. Asked afresh rather than deduced from
		 *  the job's last state: after finishing, failing or being cancelled the question is the
		 *  same one (can this install do the thing now), and the server is where that is
		 *  answered. */
		private readonly finished: () => Promise<string>,
		/** What to say when following it became impossible. */
		private readonly lost = "Couldn't follow the download. Open Activity to see it."
	) {}

	/** Whether there is a download to draw a bar for. True while it is waiting for a worker as
	 *  well as while it is fetching: both are "this is happening, do not offer the button". What
	 *  is happening is `waiting` and `status`. */
	get running(): boolean {
		return this.jobId !== null;
	}

	/** Where it is in the line, or null while nothing is being followed or nothing said. */
	position = $state<number | null>(null);

	/** Whether it has not been picked up yet, so the fraction means nothing. */
	get waiting(): boolean {
		return this.jobId !== null && WAITING_STATES.includes(this.state ?? '');
	}

	/**
	 * Where the download has got to, in the words a screen puts beside the bar.
	 *
	 * One sentence, here, because Faces, Semantic, Watermarks and the graphics-card pane all say
	 * it, and four copies built from the fraction drift apart on what to do with the job's note.
	 *
	 * The place in line comes from the server, which counts it in the order the queue is really
	 * claimed in (`JobQueue.positions_of`). A page of fifty rows and a tally by state are not a
	 * position, and "580 jobs are waiting" in place of one would be a number that is plausible and
	 * false. For a job the server gives no place to the sentence is the same shape without the
	 * number, so a build that cannot answer loses the number and nothing else.
	 *
	 * The place arrives one-based and the words say what is AHEAD, which is the question somebody
	 * watching a bar is actually asking. First in line is "Next to start" rather than "0 ahead": a
	 * nought in a sentence about waiting reads as a fault.
	 */
	get status(): string {
		if (this.waiting) return sayWaiting(this.position);
		const percent = Math.round(this.fraction * 100);
		const what = this.note ?? (this.fraction >= 1 ? 'finishing up' : 'downloading');
		return `${percent}% — ${what}`;
	}

	/** Follow a run just started here. Queued until something says otherwise: a job is on the
	 *  queue the moment it is made, and claiming to be downloading before a worker has taken it is
	 *  the fault this whole distinction exists for. */
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
		// Checked again: the read above is a round trip, and a run started while it was in flight is
		// the one to follow.
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
