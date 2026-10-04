/* The work queue's first page, read once for everybody who needs it, and reduced to what a screen
 * asks.
 *
 * Two questions are answered from one read. Is Sift taking anything in right now, and a nudge the
 * moment it stops: that is what makes a file dropped into a watched folder appear while
 * somebody is still looking at the screen. And the page itself, which the Jobs dashboard draws.
 *
 * The one connection this application holds says when the queue changes and this asks then, so
 * an idle library costs nothing at all, and a busy one costs one read a second.
 *
 * Two things it deliberately does not do:
 *
 * **It does not name the files.** What is read carries job types and states, never payloads. So a
 * file arriving from a drop is drawn by name (the browser handed it over) and a file appearing on
 * disk is a count.
 *
 * **It is admin-only, because the queue is.** A guest's grid follows files arriving instead,
 * which is the thing they can actually see; the queue is a picture of somebody's library and is
 * not theirs.
 *
 * **The Downloads row's facts are NOT read from that page.** It is the newest fifty jobs of every
 * kind, so a download can scroll off it behind a few seconds of background work before it ends,
 * and the dot would never light and the glyph stop turning while it still waited. Those come from
 * the download rows themselves (`readDownloads`), which hold how every download ended.
 */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';
import type { JobsPage } from '$lib/jobs/family';
import { OneRead } from '$lib/jobs/one-read';

/* LIVE: followed by routes/+layout.svelte (refresh and readDownloads on the jobs, downloads and settings bells) */

/*
 * The work that means "a file is on its way into the library".
 *
 * A scan is not on the list. It is Sift reading a folder to find out what is in it, which is work
 * but is not a file arriving. And a scan of a large library runs for minutes, so counting it would
 * leave "importing" on the screen for the whole of it with nothing appearing.
 *
 * `fts_reindex` is likewise housekeeping over things already here.
 */
const ARRIVING = new Set(['import', 'download', 'probe', 'thumbnail', 'preview', 'sprite']);

/* The work that begins a file's way in: a drop and a download head their own families. */
const BEGINS_ARRIVING = new Set(['import', 'download']);

const IN_FLIGHT = new Set(['queued', 'running']);

/*
 * WHETHER THIS ROW IS A FILE ARRIVING, or a picture of a file already here being made again.
 *
 * The same job types do both. A file taken in is read, and its reading hands out its pictures, each
 * as a STEP of the scan, drop or download it came with (`enqueue_child`), so every one of them has a
 * parent. A picture made again is asked for on its own: "Rebuild thumbnails", a change to the hover
 * clips' shape, a still cut again, a press on one file. Those rows head nothing and follow nothing.
 * Counted by type alone, a library whose stills were cut again at a start would say "Importing N
 * files" for as long as that ran, with nothing new anywhere.
 */
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

/* What the counters below actually read.
 *
 * Narrower than the page the dashboard draws, deliberately: this declares the handful of columns it
 * needs, so a change to any of the others cannot break it and a test does not have to build a
 * plausible job to exercise the counting. A whole page satisfies it, which is what `refresh` hands
 * in.
 */
interface Counted {
	jobs: JobRow[];
}

/* How many job ids to remember having seen before starting again.
 *
 * Only there to tell "this finished" from "this has not started yet", which matters for a few
 * seconds per file and never afterwards. Unbounded it is a set that grows for as long as a tab is
 * open, holding an id for every job that has ever passed through the window. */
const REMEMBERED = 500;

const NOT_AN_ADMIN = "You aren't signed in as an admin.";

/**
 * How long after the queue's last word the screen reads once more.
 *
 * The queue says something on every job, and the last thing it says is that the last job ended.
 * The read that answers it still carries the library's count of what is waiting from before that
 * job landed: both the queue's tally and the count are held for five seconds on clocks of their
 * own, and nothing pushes again once the queue is quiet. Activity would then draw a finished pass
 * as "Not started" for those seconds, with work waiting and nothing outstanding, until something
 * else happened to move the queue. One read past both windows closes it; only a read the queue
 * asked for schedules it, so an idle library is still asked nothing.
 */
export const SETTLE_MS = 6000;

export class Imports {
	/**
	 * How many pieces of work are in flight that will end in something new to look at.
	 *
	 * A floor rather than a total: what is read is the first page of the queue, so a library
	 * importing three hundred files at once reports the fifty it can see. That is why nothing here
	 * shows the number as a total. See how the grid words it.
	 */
	busy = $state(0);

	/**
	 * How many FILES are on their way into the library: what "Importing N files" says.
	 *
	 * Not `busy`, which counts the picture work too and answers a different question (is a picture
	 * coming for a tile with none). This counts only work that is part of a file arriving (see
	 * `arrives`), and counts each file once however many of its steps are in flight, by the file the
	 * row is about, or the row itself where it names none yet (a drop before it is taken in). A
	 * floor, like `busy`, and for the same reason.
	 */
	arriving = $state(0);

	/**
	 * How many downloads are fetching or waiting a turn they will get, counted apart from the rest of
	 * the arriving work.
	 *
	 * A download is the one kind of arriving work a person kicked off and is waiting on, so it is
	 * counted apart from the ambient "Sift is busy" turn; the Downloads rail item says it to a
	 * screen reader and turns nothing. Only the fetch itself counts: the probing and thumbnail a
	 * finished download spawns are the same background work as anything else's and belong to the
	 * general signal. One waiting behind a paused queue cannot start, so the server leaves it out.
	 */
	downloading = $state(0);

	/**
	 * In-flight work that is not a download, however it got there.
	 *
	 * What turns the gear. Not `busy`, which counts only ARRIVING work (imports, probes,
	 * thumbnails), so two hundred face scans grinding through the library would turn nothing at
	 * all, and the one place that shows the queue doing it would give no sign it was.
	 *
	 * Derived from the queue rather than from a list of job types. A list would have to be added to
	 * every time a new kind of background work appears, nothing would fail when it was not, and the
	 * gear would quietly stop turning for it. Everything that is not a download counts, because a
	 * download has an indicator of its own and would otherwise turn both.
	 */
	working = $state(0);

	/* Whether a download has ended, landed or failed, since somebody last looked. Read off the rows,
	   so it holds until the Downloads screen is opened or "Mark downloads as seen" is chosen (in any
	   window), and a reload does not relight what was seen. Failure outranks success (a red dot over
	   a green one): a person wants to know something went wrong more than that something went right. */
	downloadFailed = $state(false);
	downloadSucceeded = $state(false);

	/**
	 * How many downloads are stopped waiting for somebody to hand over cookies.
	 *
	 * Read with the rest of the Downloads row's facts: a download that needs cookies for its Site is
	 * parked `blocked` by the kernel, which is the same fact the Downloads screen draws as "Waiting
	 * for cookies" on the row.
	 */
	waitingForCookies = $state(0);

	/**
	 * How many Sites have saved cookies that have run out.
	 *
	 * The other half of the same number, and the half the queue cannot know: an expiry that has
	 * passed is a download that WILL stop, and there may be nothing in the queue yet to say so.
	 * `state` is the server's word, computed once there. See the Downloads screen for why two
	 * readers of the same dates would come to disagree.
	 */
	expiredCookies = $state(0);

	/**
	 * How many tasks are parked until somebody gives the password: a saved key sealed since Sift
	 * started. The server's count over the whole queue (`password_wanted`), read with the page this
	 * store already takes on the jobs bell, so the unlock bar comes back the moment work stops for
	 * the key, in every open window, without a read of its own.
	 */
	passwordWanted = $state(0);

	/**
	 * What the rail's Downloads item counts, and it is the same number the screen's Cookies door
	 * wears: everything that would be fixed by walking through that door.
	 */
	get cookiesWanted(): number {
		return this.waitingForCookies + this.expiredCookies;
	}

	/**
	 * Ask what is saved for each Site, and count what has run out.
	 *
	 * NOT A SECOND POLL, and that is the whole design of it. The queue's half above rides on the
	 * read this store already takes; this half is asked once when the shell opens and again only
	 * when the number of downloads waiting for cookies CHANGES, because an expiry that has quietly
	 * passed matters at the moment a download hits it, and that download is exactly what blocks. A
	 * timer here would be a request a second for a date that moves twice a month.
	 *
	 * Asked from `refresh` rather than from the rail that draws it. The rail is one of several
	 * screens that could want the number, and a component that fetches for itself is a request per
	 * mount, and, in a unit test, a component that reaches the network to be drawn at all.
	 *
	 * Silent on a failure: a rail that cannot say how many Sites are asking for something says
	 * nothing.
	 */
	async readCookies(): Promise<void> {
		if (this.#refused) return;
		try {
			const saved = await api.get<components['schemas']['ConnectionItem'][]>('/site-connections');
			this.expiredCookies = saved.filter((one) => one.state === 'expired').length;
		} catch {
			// See above.
		}
	}

	/** The Downloads rail dot: an outcome not yet seen, or none. Failure outranks success. A running
	    download lights no dot and turns nothing on the rail: the Downloads screen shows it. Put out by
	    opening the Downloads screen, or by "Mark downloads as seen". */
	get downloadStatus(): 'error' | 'success' | 'none' {
		if (this.downloadFailed) return 'error';
		if (this.downloadSucceeded) return 'success';
		return 'none';
	}

	/** The outcomes have been seen: the dot goes out here at once, and the rows are marked, so it is
	 *  out in every other window too and a reload does not bring it back. A mark that could not be
	 *  sent leaves the dot to come back on the next read, which is the honest answer. */
	clearDownloadStatus(): void {
		this.downloadFailed = false;
		this.downloadSucceeded = false;
		void api.post('/downloads/seen', {}).catch(() => {
			// See above: the next read says what the rows say.
		});
	}

	/**
	 * Read what the Downloads row on the rail says, off the download rows.
	 *
	 * Asked when the connection says the downloads, the queue or a setting moved: waiting for cookies
	 * is the job's state, and the pause is a setting. Silent on a failure, keeping what it last held.
	 */
	async readDownloads(): Promise<void> {
		if (this.#refused) return;
		const turn = this.#turn;
		try {
			const waiting = this.waitingForCookies;
			const facts = await api.get<Glance>('/downloads/glance');
			if (turn !== this.#turn) return;
			this.glance(facts);
			// The first read, and every time the number of downloads stopped for cookies moves. See
			// `readCookies` for why that is the moment and why there is no timer.
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

	/** The queue's first page as it was last read, or null before the first one. What the dashboard
	 *  draws, held here so that the dashboard and the busy indicator are one read rather than two. */
	page = $state<JobsPage | null>(null);

	/** What went wrong reading it, in a sentence, or null. Kept rather than thrown: this is a live
	 *  view of something that may simply be unreachable for a moment, and an exception would take
	 *  the last good page off the screen along with it. */
	problem = $state<string | null>(null);

	/** True once a read has succeeded and nothing has refused since. What a screen shows as "live". */
	live = $state(false);

	/* Set when the server says this account may not look, which is a stop rather than a hiccup. The
	   nav item is hidden from a guest but the address is typeable, and retrying forever on behalf of
	   somebody who is never going to be let in is what a refusal is not. */
	#refused = false;

	/* The last read, kept apart so `landed` can answer about one job rather than the queue (see
	 * `landed` for why following a job's children would be wrong).
	 *
	 * Not reactive on purpose: rebuilding two collections a second and having Svelte compare
	 * them is work for nothing. `pulse` is what callers depend on.
	 */
	#active = new Set<string>();
	#seen = new Set<string>();
	/* Whether the saved cookies have been asked about at all yet. See `readCookies`. */
	#askedAboutCookies = false;

	/* How many scans were in flight at the last read.
	 *
	 * A scan is deliberately NOT counted as arriving work. It does not touch `busy`, so "importing
	 * files" does not sit on the screen for the minutes one runs over a large library. But a scan
	 * FINISHING is a catalog change the grid has to re-read, and one that nothing else announces: adding
	 * a folder of files Sift already knows links them back into view without enqueuing a probe, so there
	 * is no arriving work to tick `settled`. When this count drops, a scan has finished, and `settled`
	 * ticks so the grid re-asks and those files appear without a reload. */
	#scanning = 0;

	/* The one read scheduled after the queue's last word. See `SETTLE_MS`. */
	#settling: ReturnType<typeof setTimeout> | null = null;

	/** Read it now, whether or not anything said to.
	 *
	 *  Called once when the screen opens, and again whenever the connection says the queue moved.
	 *  There is no timer: an idle library says nothing, so nothing is asked. */
	/** Forget a refusal, because somebody else is signed in now.
	 *
	 *  A refusal is remembered for the life of the tab, which outlives a sign-out: without this a
	 *  guest signing out and an admin signing in on the same tab would find the indicator silent for
	 *  good, with nothing on screen to say why.
	 */
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

	/* Whether a read still owed should settle (see `#settleLater`). Any bell rung while a read was
	   out asks for it; the settling read's own ask does not. */
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

	/** Read once more after the queue has gone quiet, where a pass shows work waiting and
	 *  nothing outstanding: the count behind that word may be the one taken before the last job
	 *  landed. The settling read itself schedules nothing, so a pass that is genuinely not
	 *  started costs one extra read per queue movement and no more. */
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

	/**
	 * Take one page of the queue and reduce it to what a screen needs.
	 *
	 * Public because it is the seam. Everything above it is fetching, and everything interesting is
	 * below it: what counts as a file arriving, when to say something landed, and which job is done
	 * with. A test that had to reach the network for any of that would be testing the network.
	 */
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
		/* Waiting for its moment is not working.
		 *
		 * Whole-library jobs (grouping faces, looking for duplicates) are held back until the
		 * batch that asked for them stops arriving, so a thousand imported files rebuild the same
		 * answer once instead of a thousand times. That is a minute of sitting in the queue doing
		 * nothing, and the gear must not turn for it: the indicator saying Sift is busy while the
		 * machine is idle is backwards from what it is for.
		 *
		 * `run_after` is unix SECONDS and `Date.now()` is milliseconds; compare them with the
		 * conversion.
		 */
		const due = Date.now() / 1000;
		this.working = page.jobs.filter(
			(job) =>
				IN_FLIGHT.has(job.state) &&
				job.type !== 'download' &&
				(job.run_after === null || job.run_after === undefined || job.run_after <= due)
		).length;
		this.pulse += 1;

		// Fewer than last time means some of it landed. Rising is a file being taken in and is not
		// worth re-asking for: there is nothing to show yet, and the tile that would answer it does
		// not exist until the work this is counting has finished.
		if (this.busy < before) this.settled += 1;

		// A scan finishing re-asks the grid as well, for the files it linked back into view without any
		// arriving work to announce them (see `#scanning`). Counted apart from `busy` on purpose, so a
		// running scan does not read as "importing files".
		const scanningNow = page.jobs.filter(
			(job) => job.type === 'scan' && IN_FLIGHT.has(job.state)
		).length;
		if (scanningNow < this.#scanning) this.settled += 1;
		this.#scanning = scanningNow;
	}

	/**
	 * Whether one job itself is done with, deliberately NOT the work it went on to start.
	 *
	 * A dropped file is an `import` that enqueues a probe, which enqueues a thumbnail and a
	 * preview. The library ROW exists the moment the import alone is done, so the grid draws a real
	 * tile for it, still shimmering, because it has no picture yet. Holding the placeholder until
	 * the last of the family finished would put the placeholder beside that tile: one file, two
	 * placeholders, in two different styles, for as long as a thumbnail takes.
	 *
	 * So the handover is at the import: the placeholder goes, the tile takes over, and the tile is
	 * the better stand-in anyway: it is the file's real shape rather than a guessed square.
	 *
	 * False for a job never seen in a read, which is the important half: this is asked the instant
	 * a file is accepted, before the queue's next read carries it, and "not in flight" would then
	 * be indistinguishable from "already finished" for every file, every time.
	 */
	landed(jobId: string): boolean {
		return this.#seen.has(jobId) && !this.#active.has(jobId);
	}
}

export const imports = new Imports();
