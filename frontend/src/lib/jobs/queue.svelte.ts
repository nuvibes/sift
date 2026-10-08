import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { SvelteSet } from 'svelte/reactivity';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { JobsPage, StepsPage } from './family';
import { OneRead } from './one-read';

/* LIVE: followed by lib/jobs/JobsScreen.svelte (refresh on the jobs bell) */

export type JobState = components['schemas']['JobState'];

/* The states a job is over in, taken from the type the server generates.
 *
 * Written down once, here: a copy that watched for `succeeded` (not a state Sift has; the queue
 * says `done`) would never recognise a finished download, and its bar would sit at whatever
 * fraction it last read, forever.
 *
 * Typed as JobState rather than as strings, so a name that is not one fails to compile instead of
 * failing to match.
 */
const FINISHED_STATES: readonly JobState[] = ['done', 'failed', 'canceled'];

/** Whether a job is over, however it ended. */
export function isFinished(state: string): boolean {
	return (FINISHED_STATES as readonly string[]).includes(state);
}

/* Where the dashboard's numbers come from.
 *
 * The default view (everything, newest first) is a page of FAMILIES (`?fold=true`): one row
 * per download or scan, its steps counted and folded under it. Choosing a state asks for the
 * families whose row shows it (`?fold=true&state=failed`), so a tab lists exactly the families
 * its number counts and the numbers add up to All. Choosing a kind asks for that kind's tasks,
 * flat.
 *
 * NOT THE PAGE THE BUSY INDICATOR READS, because they ask different questions: the indicator
 * counts rows (downloads under way, work running) and needs every step; this screen draws
 * families and must not have them. So while Activity is open a change in the queue costs two
 * reads rather than one. Sharing one read would mean the indicator counting a folded page, where
 * a download's seven running steps are one row, or this screen drawing a handful of downloads per
 * fifty rows.
 *
 * Filtering the fifty rows already in hand is not the same thing and would put a number above the
 * table that disagrees with it: the tallies describe the whole queue, so "Failed 61" over a list
 * of the three failures that happened to be in the last fifty rows is a screen that lies quietly.
 *
 * Neither case is on a timer. The connection this application holds says when the queue has moved
 * and the screen asks then, so a library with nothing happening costs nothing at all, rather
 * than a page built on the server every second for as long as a tab stays open.
 */

const NOT_ALLOWED = "You aren't signed in as an admin.";

/** How many of a family's steps one read brings: the server's own default page. */
const STEPS_PAGE = 50;

/** The most one read may ask for (the server's ceiling). */
const MOST_PER_READ = 200;

/**
 * How many rows a page of the list holds. Fixed, so a page is the same rows on every screen and
 * the pager under the list can name its pages by number.
 */
export const QUEUE_PAGE = 20;

/** What a pause or a cancel names: a pass by its key, a sub-task by its job type, or neither. */
export type Which = components['schemas']['WhichWork'];

/**
 * The Type choice's one entry for every kind this version of Sift has no handler for (the page's
 * `older`): rows an older release left in the table, asked for as `?older=true` and never by their
 * stored ids. Not a job type id: those are lower-case words joined by underscores.
 */
export const OLDER_KINDS = ':older';

export class Queue {
	filter = $state<JobState | null>(null);
	/** The one kind of task shown, by its type, or null for every kind. The list's Type narrowing. */
	kind = $state<string | null>(null);
	/** Where the page being read starts, counting from zero. Back to the first on a new filter. */
	offset = $state(0);

	#page = $state<JobsPage | null>(null);
	/* Which filter the held page answers (the state and the kind, as one key). A new filter keeps
	   the old page until its own lands, so the tallies and the task rows above the list stay put
	   while only the list waits. */
	#answers = $state<string | null>(null);
	#problem = $state<string | null>(null);
	#live = $state(false);
	/* An admin-only address refused us: asking again would be asking on behalf of somebody who is
	   never getting in. */
	#refused = false;

	/* The bell rings for every job that moves: one read at a time (see `OneRead`). */
	#reads = new OneRead(() => this.#readOnce());

	/** Which families are opened out, by their top's id. Folded by default: nothing is here. */
	#open = new SvelteSet<string>();
	/** Each opened family's steps as last read. */
	#steps = $state<Record<string, StepsPage>>({});

	/** The last page read, whichever filter asked: the tallies and the task rows read this. */
	get page(): JobsPage | null {
		return this.#page;
	}

	/** What the list draws: families while everything is showing, matching steps for a state. Null
	 *  until a page for the filter chosen now has landed. */
	get listed(): JobsPage | null {
		return this.#answers === this.#asking() ? this.#page : null;
	}

	/* The question the list is asking now, as one key: which state, and which kind. */
	#asking(): string {
		return `${this.filter ?? ''}|${this.kind ?? ''}`;
	}

	/** True once a read has landed, and while the last one succeeded. */
	get live(): boolean {
		return this.#live;
	}

	/**
	 * What went wrong, in a sentence, or null. Kept rather than thrown: the screen is a live view of
	 * something that may simply be unreachable for a moment, and an exception would take the last
	 * good page off the screen along with it.
	 */
	get problem(): string | null {
		return this.#problem;
	}

	/** Whether every kind is showing, which is the view that folds, with or without a state: a
	 *  state's tab lists the families whose row shows it. A kind narrows to the tasks of that
	 *  kind, flat: a step of an import is a task of its own kind, and a folded page counts only
	 *  the families that START with that kind, so it would list none of them. */
	get folded(): boolean {
		return this.kind === null;
	}

	/** Show one state, or everything. Settles once that state's page has landed. */
	setFilter(state: JobState | null): Promise<void> {
		if (this.filter === state) return Promise.resolve();
		this.filter = state;
		this.offset = 0;
		return this.refresh();
	}

	/** Show one kind of task, or every kind. Back to the first page, like a new state. */
	setKind(kind: string | null): Promise<void> {
		if (this.kind === kind) return Promise.resolve();
		this.kind = kind;
		this.offset = 0;
		return this.refresh();
	}

	/** Read the page starting at `offset`, counting from zero. */
	goTo(offset: number): Promise<void> {
		this.offset = Math.max(0, offset);
		return this.refresh();
	}

	/** Whether this family is opened out. */
	isOpen(id: string): boolean {
		return this.#open.has(id);
	}

	/** An opened family's steps, or null before the first read of them lands. */
	stepsOf(id: string): StepsPage | null {
		return this.#steps[id] ?? null;
	}

	/** Open a family out, or fold it again. Its steps are read when it opens, never before. */
	async toggle(id: string): Promise<void> {
		if (this.#open.has(id)) {
			this.#open.delete(id);
			delete this.#steps[id];
			return;
		}
		this.#open.add(id);
		await this.#readSteps(id, STEPS_PAGE);
	}

	/** The next page of an opened family's steps, after the ones already shown. */
	async moreSteps(id: string): Promise<void> {
		const shown = this.#steps[id]?.jobs.length ?? 0;
		await this.#readSteps(id, shown + STEPS_PAGE);
	}

	/*
	 * READ AN OPENED FAMILY'S FIRST `wanted` STEPS, a page at a time from the start.
	 *
	 * From the start every time rather than appending the next page to what is held: the steps move
	 * while the row is open (a step finishes, the next starts), and a page appended to a list read a
	 * minute ago would be a list of two different moments.
	 */
	async #readSteps(id: string, wanted: number): Promise<void> {
		const jobs: StepsPage['jobs'] = [];
		let total = 0;
		let atLeast = false;
		try {
			while (jobs.length < wanted) {
				const limit = Math.min(MOST_PER_READ, wanted - jobs.length);
				const page = await api.get<StepsPage>(`/jobs/${id}/steps`, {
					query: { limit, offset: jobs.length }
				});
				jobs.push(...page.jobs);
				total = page.total;
				atLeast = page.at_least;
				if (page.jobs.length < limit) break;
			}
		} catch (error) {
			// Gone: the family was pruned or finished and cleared while it was open.
			if (error instanceof ApiError && error.status === 404) {
				this.#open.delete(id);
				delete this.#steps[id];
			}
			return;
		}
		// Folded again while the read was out: what came back is for a row nobody is looking at.
		if (!this.#open.has(id)) return;
		this.#steps[id] = { jobs, total, at_least: atLeast };
	}

	async retry(id: string): Promise<void> {
		await api.post(`/jobs/${id}/retry`);
		await this.refresh();
	}

	/** Everything that failed, back in the queue. Answers how many there were. */
	async retryFailed(): Promise<number> {
		const { retried } = await api.post<components['schemas']['Retried']>('/jobs/retry-failed');
		await this.refresh();
		return retried;
	}

	/* Everything that failed, thrown away. Answers how many went.
	 *
	 * The other half of the pair. Running them again is the right answer to a failure whose cause
	 * has been dealt with; it is no answer at all to one that cannot succeed: a card that was
	 * not installed at the time, a drive that has gone. Those accumulate: a failed job is the one
	 * ending nothing clears out on its own.
	 */
	async clearFailed(): Promise<number> {
		const { cleared } = await api.post<components['schemas']['Cleared']>('/jobs/clear-failed');
		await this.refresh();
		return cleared;
	}

	/* Everything that was stopped, started again. Answers how many there were.
	 *
	 * The other half of `cancelAll`: stopping an import that got away is one press, and putting
	 * it back is one press too. Without it the only route to the same work would be scanning the
	 * folders again, which re-walks every file to rediscover the ones it already knew about:
	 * minutes of walking to recover work still sitting in the table with its payload intact.
	 *
	 * Failures are left where they are. Stopped and failed wear the same "unfinished" label and
	 * are not the same thing: one is a decision being taken back, the other is work that broke
	 * and will likely break again. `retryFailed` is the button for the second.
	 */
	async retryCanceled(): Promise<number> {
		const { retried } = await api.post<components['schemas']['Retried']>('/jobs/retry-canceled');
		await this.refresh();
		return retried;
	}

	/* Everything that was stopped, thrown away. Answers how many rows went.
	 *
	 * The pile `clearFailed` does not touch, and the one that actually piles up: a stop is one press
	 * that cancels the whole queue, so a library-sized import leaves a library-sized heap behind.
	 * They are only swept a week after they were stopped, which is the right pace for housekeeping
	 * and no answer to somebody looking at a screen made of them today. */
	async clearCanceled(): Promise<number> {
		const { cleared } = await api.post<components['schemas']['Cleared']>('/jobs/clear-canceled');
		await this.refresh();
		return cleared;
	}

	/* Drawn canceled on the press, over any read that left before it, and put back on a refusal. */
	async cancel(id: string): Promise<void> {
		this.#canceling.add(id);
		this.#page = this.#withCancels(this.#page);
		try {
			await api.post(`/jobs/${id}/cancel`);
		} catch (error) {
			this.#canceling.delete(id);
			await this.refresh();
			throw error;
		}
		try {
			await this.refresh();
		} finally {
			this.#canceling.delete(id);
		}
	}

	/* The jobs a cancel is out for, and the page with them drawn as canceled. */
	#canceling = new Set<string>();

	#withCancels(page: JobsPage | null): JobsPage | null {
		if (page === null || this.#canceling.size === 0) return page;
		return {
			...page,
			jobs: page.jobs.map((job) =>
				this.#canceling.has(job.id) && !FINISHED_STATES.includes(job.state)
					? { ...job, state: 'canceled' as const }
					: job
			)
		};
	}

	/* Everything that has not finished, called off. Answers how many were stopped.
	 *
	 * The queue that got away is not something a row at a time can answer: a folder that held far
	 * more than anybody meant to point at leaves tens of thousands of jobs, and the alternative
	 * without this is closing the application.
	 *
	 * It stops running work as well as waiting work, which is what makes it mean anything: the
	 * rows in the queue were put there by a scan that is still walking, so calling off only what is
	 * waiting would leave the producer running and the queue would refill behind the press. */
	async cancelAll(): Promise<number> {
		const { stopped } = await api.post<components['schemas']['Stopped']>('/jobs/cancel-all');
		await this.refresh();
		return stopped;
	}

	/* A pass, one of its sub-tasks, or with neither the whole queue: what a pause, a resume or a
	   cancel acts on. A pause holds what starts next; what runs finishes the step in its hand. */
	async pause(which: Which, paused: boolean): Promise<void> {
		await api.post(paused ? '/jobs/pause' : '/jobs/resume', { body: which });
		await this.refresh();
	}

	/* Every unfinished task of a pass or of one sub-task, called off. Answers how many. */
	async cancelWork(which: Which): Promise<number> {
		const { stopped } = await api.post<components['schemas']['Stopped']>('/jobs/cancel-work', {
			body: which
		});
		await this.refresh();
		return stopped;
	}

	/** Whether somebody paused the whole queue. */
	get paused(): boolean {
		return this.#page?.paused ?? false;
	}

	/**
	 * Ask now rather than waiting to be told.
	 *
	 * Called when the screen opens, whenever the connection says the queue moved, and straight after
	 * retry and cancel so the row changes under the click instead of up to a second later. A second
	 * is a long time to wonder whether a button did anything.
	 */
	refresh(): Promise<void> {
		return this.#reads.ask();
	}

	async #readOnce(): Promise<void> {
		if (this.#refused) return;
		const asked = this.#asking();
		const state = this.filter;
		const kind = this.kind;
		const at = this.offset;
		try {
			const paging = { limit: QUEUE_PAGE, offset: at };
			const narrowed = {
				...(state === null ? {} : { state }),
				...(kind === null ? {} : kind === OLDER_KINDS ? { older: true } : { type: kind })
			};
			const page = await api.get<JobsPage>('/jobs', {
				query: this.folded
					? { fold: true, ...(state === null ? {} : { state }), ...paging }
					: { ...narrowed, ...paging }
			});
			// The filter or the page moved while this was out: it answers a question nobody is
			// asking now.
			if (this.#asking() !== asked || this.offset !== at) return;
			/* A page past the end (the rows it held were cleared, or finished and pruned) is read
			   again at the last page there is, rather than drawn as an empty list over a count. */
			if (at > 0 && at >= page.total) {
				this.offset = Math.max(0, Math.floor((page.total - 1) / QUEUE_PAGE) * QUEUE_PAGE);
				this.#reads.again();
				return;
			}
			this.#page = this.#withCancels(page);
			this.#answers = asked;
			this.#problem = null;
			this.#live = true;
		} catch (error) {
			this.#live = false;
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				this.#problem = NOT_ALLOWED;
				this.#refused = true;
				return;
			}
			// The one sentence for a server that did not answer, not a wording of this screen's
			// own.
			this.#problem = error instanceof ApiError ? error.message : UNREACHABLE;
			return;
		}
		// The families somebody has open move with the queue, so they are read again with it.
		await Promise.all(
			[...this.#open].map((id) =>
				this.#readSteps(id, Math.max(STEPS_PAGE, this.#steps[id]?.jobs.length ?? 0))
			)
		);
	}
}
