/* The work queue's first page, read once for everybody who needs it, and reduced to what a
 * screen asks. */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';
import type { JobsPage } from '$lib/jobs/family';
import { OneRead } from '$lib/jobs/one-read';

/* LIVE: followed by routes/+layout.svelte (refresh and readDownloads on the jobs, downloads and settings bells) */

/* The work that means "a file is on its way into the library". */
const ARRIVING = new Set(['import', 'download', 'probe', 'thumbnail', 'preview', 'sprite']);

/* The work that begins a file's way in: a drop and a download head their own families. */
const BEGINS_ARRIVING = new Set(['import', 'download']);

const IN_FLIGHT = new Set(['queued', 'running']);

/* WHETHER THIS ROW IS A FILE ARRIVING, or a picture of a file already here being made again. */
function arrives(job: JobRow): boolean {
	if (!IN_FLIGHT.has(job.state) || !ARRIVING.has(job.type)) return false;
	return BEGINS_ARRIVING.has(job.type) || (job.parent_id !== null && job.parent_id !== undefined);
}

/** What the Downloads row on the rail says, as the server reads it off the download rows. */
export type Glance = components['schemas']['DownloadsAtAGlance'];

/** Nothing fetching, nothing waiting, nothing unseen: a window before its first read. */
export const NOTHING_TO_GLANCE_AT: Glance = {
	downloading: 0,
	waiting_for_cookies: 0,
	landed_unseen: 0,
	failed_unseen: 0
};

type JobRow = Pick<
	components['schemas']['JobView'],
	'id' | 'parent_id' | 'type' | 'state' | 'run_after'
> &
	Partial<Pick<components['schemas']['JobView'], 'subject_id'>>;

/* What the counters below actually read. */
interface Counted {
	jobs: JobRow[];
}

/* How many job ids to remember having seen before starting again. */
const REMEMBERED = 500;

const NOT_AN_ADMIN = "You aren't signed in as an admin.";

/** How long after the queue's last word the screen reads once more. */
export const SETTLE_MS = 6000;

export class Imports {
	/** How many pieces of work are in flight that will end in something new to look at. */
	busy = $state(0);

	/** How many FILES are on their way into the library: what "Importing N files" says. */
	arriving = $state(0);

	/** How many downloads are fetching or waiting a turn they will get, counted apart from the
	 * rest of the arriving work. */
	downloading = $state(0);

	/** In-flight work that is not a download, however it got there. */
	working = $state(0);

	/* Whether a download has ended, landed or failed, since somebody last looked. */
	downloadFailed = $state(false);
	downloadSucceeded = $state(false);

	/** How many downloads are stopped waiting for somebody to hand over cookies. */
	waitingForCookies = $state(0);

	/** How many Sites have saved cookies that have run out. */
	expiredCookies = $state(0);

	/** How many tasks are parked until somebody gives the password: a saved key sealed since Sift
	 * started. */
	passwordWanted = $state(0);

	/** What the rail's Downloads item counts, and it is the same number the screen's Cookies door
	 * wears: everything that would be fixed by walking through that door. */
	get cookiesWanted(): number {
		return this.waitingForCookies + this.expiredCookies;
	}

	/** Ask what is saved for each Site, and count what has run out. */
	async readCookies(): Promise<void> {
		if (this.#refused) return;
		try {
			const saved = await api.get<components['schemas']['ConnectionItem'][]>('/site-connections');
			this.expiredCookies = saved.filter((one) => one.state === 'expired').length;
		} catch {
			// See above.
		}
	}

	/** The Downloads rail dot: an outcome not yet seen, or none. */
	get downloadStatus(): 'error' | 'success' | 'none' {
		if (this.downloadFailed) return 'error';
		if (this.downloadSucceeded) return 'success';
		return 'none';
	}

	/** The outcomes have been seen: the dot goes out here immediately, and the rows are marked,
	 * so it is out in every other window too and a reload does not bring it back. */
	clearDownloadStatus(): void {
		this.downloadFailed = false;
		this.downloadSucceeded = false;
		void api.post('/downloads/seen', {}).catch(() => {
			// See above: the next read says what the rows say.
		});
	}

	/** Read what the Downloads row on the rail says, off the download rows. */
	async readDownloads(): Promise<void> {
		if (this.#refused) return;
		const turn = this.#turn;
		try {
			const waiting = this.waitingForCookies;
			const facts = await api.get<Glance>('/downloads/glance');
			if (turn !== this.#turn) return;
			this.glance(facts);
			// The first read, and every time the number of downloads stopped for cookies moves.
			if (!this.#askedAboutCookies || this.waitingForCookies !== waiting) {
				this.#askedAboutCookies = true;
				void this.readCookies();
			}
		} catch {
			// See above.
		}
	}

	/** Take one read of the Downloads row's facts. The seam, like `apply`: nothing here fetches. */
	glance(facts: Glance): void {
		this.downloading = facts.downloading;
		this.waitingForCookies = facts.waiting_for_cookies;
		this.downloadFailed = facts.failed_unseen > 0;
		this.downloadSucceeded = facts.landed_unseen > 0;
	}

	/** Rises each time work finishes. Anything drawing from the library can watch this and re-ask. */
	settled = $state(0);

	/** Rises on every read. For anything derived from the state below, which is not itself reactive. */
	pulse = $state(0);

	/** The queue's first page as it was last read, or null before the first one. */
	page = $state<JobsPage | null>(null);

	/** What went wrong reading it, in a sentence, or null. */
	problem = $state<string | null>(null);

	/** True once a read has succeeded and nothing has refused since. What a screen shows as "live". */
	live = $state(false);

	/* Set when the server says this account may not look, which is a stop rather than a hiccup. */
	#refused = false;

	/* The last read, kept apart so `landed` can answer about one job rather than the queue (see
	 * `landed` for why following a job's children would be wrong). */
	#active = new Set<string>();
	#seen = new Set<string>();
	/* Whether the saved cookies have been asked about at all yet. See `readCookies`. */
	#askedAboutCookies = false;

	/* How many scans were in flight at the last read. */
	#scanning = 0;

	/* The one read scheduled after the queue's last word. See `SETTLE_MS`. */
	#settling: ReturnType<typeof setTimeout> | null = null;

	/** Read it now, whether or not anything said to. */
	/** Forget a refusal, because somebody else is signed in now. */
	forgetRefusal(): void {
		this.#refused = false;
	}

	/* Which session a read was asked for. `stop` moves it, so a read still out is dropped. */
	#turn = 0;

	/** Ask nothing more for the session that is ending: the read due later, and any still out. */
	stop(): void {
		this.#turn += 1;
		this.#reads.forget();
		this.#settleOwed = false;
		if (this.#settling !== null) clearTimeout(this.#settling);
		this.#settling = null;
		this.live = false;
		this.#quiet();
	}

	/* One read at a time (see `OneRead`): the jobs bell rings for every job that moves, and the
	   rail, the grid and the unlock bar all follow this one store. */
	#reads = new OneRead(() => this.#readOnce());

	/* Whether a read still owed should settle (see `#settleLater`). */
	#settleOwed = false;

	refresh(settle = true): Promise<void> {
		if (settle) this.#settleOwed = true;
		return this.#reads.ask();
	}

	async #readOnce(): Promise<void> {
		if (this.#refused) return;
		const turn = this.#turn;
		const settle = this.#settleOwed;
		this.#settleOwed = false;
		try {
			const page = await api.get<JobsPage>('/jobs');
			if (turn !== this.#turn) return;
			this.page = page;
			this.problem = null;
			this.live = true;
			this.apply(page);
			this.passwordWanted = page.password_wanted ?? 0;
			if (settle) this.#settleLater(page);
		} catch (error) {
			if (turn !== this.#turn) return;
			this.live = false;
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				this.problem = NOT_AN_ADMIN;
				// Not an admin. There is nothing to come back for, and the counters go quiet rather
				// than keeping whatever they last held.
				this.#refused = true;
				this.#quiet();
				return;
			}
			this.problem = error instanceof ApiError ? error.message : UNREACHABLE;
		}
	}

	/** Read once more after the queue has gone quiet, where a pass shows work waiting and nothing
	 * outstanding: the count behind that word may be the one taken before the last job landed. */
	#settleLater(page: JobsPage): void {
		if (this.#settling !== null) {
			clearTimeout(this.#settling);
			this.#settling = null;
		}
		const families = Object.values(page.families ?? {});
		if (families.some((one) => one.outstanding > 0)) return;
		const unsettled = families.some(
			(one) => one.waiting > 0 && one.on && one.ready && one.reason === null
		);
		if (!unsettled) return;
		this.#settling = setTimeout(() => {
			this.#settling = null;
			void this.refresh(false);
		}, SETTLE_MS);
	}

	/** Stop saying anything is happening. Used when this account turns out not to be allowed. */
	#quiet(): void {
		this.busy = 0;
		this.arriving = 0;
		this.downloading = 0;
		this.working = 0;
		this.waitingForCookies = 0;
		this.expiredCookies = 0;
		this.passwordWanted = 0;
		this.page = null;
	}

	/** Take one page of the queue and reduce it to what a screen needs. */
	apply(page: Counted): void {
		const before = this.busy;

		this.#active = new Set();
		if (this.#seen.size > REMEMBERED) this.#seen = new Set();

		for (const job of page.jobs) {
			this.#seen.add(job.id);
			if (IN_FLIGHT.has(job.state)) this.#active.add(job.id);
		}

		this.busy = page.jobs.filter(
			(job) => ARRIVING.has(job.type) && IN_FLIGHT.has(job.state)
		).length;
		this.arriving = new Set(page.jobs.filter(arrives).map((job) => job.subject_id ?? job.id)).size;
		/* Waiting for its moment is not working. */
		const due = Date.now() / 1000;
		this.working = page.jobs.filter(
			(job) =>
				IN_FLIGHT.has(job.state) &&
				job.type !== 'download' &&
				(job.run_after === null || job.run_after === undefined || job.run_after <= due)
		).length;
		this.pulse += 1;

		// Fewer than last time means some of it landed.
		if (this.busy < before) this.settled += 1;

		// A scan finishing re-asks the grid as well, for the files it linked back into view without
		// any arriving work to announce them (see `#scanning`).
		const scanningNow = page.jobs.filter(
			(job) => job.type === 'scan' && IN_FLIGHT.has(job.state)
		).length;
		if (scanningNow < this.#scanning) this.settled += 1;
		this.#scanning = scanningNow;
	}

	/** Whether one job itself is done with, deliberately NOT the work it went on to start. */
	landed(jobId: string): boolean {
		return this.#seen.has(jobId) && !this.#active.has(jobId);
	}
}

export const imports = new Imports();
