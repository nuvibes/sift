// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Saying that Sift is benchmarking this device, and what it set, on whatever screen is open.
 *
 * ## What it is about
 *
 * Adding the first library folder on a device never measured queues the benchmark, and when it
 * ends Sift sets what it found (`performance/benchmark.py` on the server). Two things are said, in
 * the window of whoever added the folder and in every other admin window: that it is running, with
 * Open to the benchmark's row, and how it ended, with Review to the same row and its results.
 *
 * ## How a move is noticed
 *
 * The way a finished download is (`toasts-downloads.svelte.ts`): the bell carries a word, never a
 * row, so each ring of the jobs bell (the run is a job) and the library bell (a folder was added)
 * reads `GET /performance/benchmark` and compares the run's id and state with what this tab has
 * said. The first read only remembers: opening Sift is not the moment to be told about a run that
 * ended before. A tab stops asking once the server says nothing more runs by itself (`measured`).
 *
 * ## Whose words
 *
 * The sentence is the server's (`said`), the one Activity's note carries for the same moment, so
 * a toast and the line somebody finds later on Activity cannot come to say different things. The
 * two presses are this screen's words (`Performance.search.ts`).
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { jobChanges, libraryChanges } from '$lib/library/changes.svelte';
import { COPY, MEASURE_ROW } from '$lib/settings-ui/Performance.search';
import { openSettings } from '$lib/settings-ui/settings-view';
import { toasts } from '$lib/shell/toasts.svelte';

/** What the route answers: the run, its sentence, and whether this device is measured. */
export type FirstBenchmarkRead = Pick<
	components['schemas']['FirstBenchmarkView'],
	'state' | 'job_id' | 'said' | 'measured' | 'held'
>;

/** One thing to say: the server's sentence, how it reads, and which press it carries. */
export interface BenchmarkToast {
	/** Which moment of which run, so each is said once: `<job id>:start` or `<job id>:end`. */
	key: string;
	message: string;
	tone: 'info' | 'success' | 'error';
	/** Open while it runs and on a failure; Review once it ended; none once it made way. */
	press: 'open' | 'review' | null;
	/** Still running: the toast stays until the run ends rather than leaving on a timer. */
	going: boolean;
}

const GOING = new Set(['waiting', 'running']);
/* Sift stopped its own full run for other work: nothing went wrong and nothing was set. */
const GAVE_WAY = 'gave_way';

/**
 * THE RULE: what one read of the run says, given what has been said already.
 *
 * A run going says it is running, once. A run that ended says how, once: what it set (Review), that
 * the settings already suited the device (Review), or why it could not run (Open, to run it by
 * hand). A run that found the device already benchmarked says nothing: a pressed run got there
 * first and is waiting for its own Apply.
 */
export function benchmarkToasts(
	read: FirstBenchmarkRead,
	said: ReadonlySet<string>
): BenchmarkToast[] {
	const id = read.job_id;
	const message = read.said;
	if (!id || !message || read.state === 'none' || read.state === 'already') return [];
	if (GOING.has(read.state)) {
		const key = `${id}:start`;
		return said.has(key) ? [] : [{ key, message, tone: 'info', press: 'open', going: true }];
	}
	const key = `${id}:end`;
	if (said.has(key)) return [];
	const failed = read.state === 'failed';
	if (read.state === GAVE_WAY) return [{ key, message, tone: 'info', press: null, going: false }];
	return [
		{
			key,
			message,
			tone: failed ? 'error' : 'success',
			press: failed ? 'open' : 'review',
			going: false
		}
	];
}

/** The keys a read stands for, said or not: what the first read remembers without saying. */
function keysOf(read: FirstBenchmarkRead): string[] {
	if (!read.job_id) return [];
	const start = `${read.job_id}:start`;
	return GOING.has(read.state) ? [start] : [start, `${read.job_id}:end`];
}

/**
 * The listener: one per tab, started by the toaster once an admin is signed in.
 *
 * `read` and `say` are handed in so a test can plant the server and listen to what is said.
 */
export class BenchmarkToasts {
	/**
	 * While the run waits or runs, the server's sentence for the first folder it holds back
	 * (`held`), else null: the wall says it in place of files nothing is reading yet. Kept on
	 * every read, the first one included, so a tab opened mid-run says it too.
	 */
	held = $state<string | null>(null);

	#said = new Set<string>();
	#first = true;
	#finished = false;
	#started = false;
	#reading = false;
	#again = false;
	/** The toast of the run still going in this tab, so its end can take its place. */
	#running: number | null = null;

	readonly #read: () => Promise<FirstBenchmarkRead>;
	readonly #say: (toast: BenchmarkToast) => number;
	readonly #unsay: (id: number) => void;

	constructor(
		read: () => Promise<FirstBenchmarkRead> = readRun,
		say: (toast: BenchmarkToast) => number = showToast,
		unsay: (id: number) => void = (id) => toasts.dismiss(id)
	) {
		this.#read = read;
		this.#say = say;
		this.#unsay = unsay;
	}

	/** Begin listening, once, for an admin; a guest adds no folder and sees no benchmark. */
	follow(isAdmin: boolean): void {
		if (!isAdmin || this.#started) return;
		this.#started = true;
		jobChanges.subscribe(() => void this.look());
		libraryChanges.subscribe(() => void this.look());
		void this.look();
	}

	/** One ring: read, compare, say. One read at a time; a ring during it reads again. */
	async look(): Promise<void> {
		if (this.#finished) return;
		if (this.#reading) {
			this.#again = true;
			return;
		}
		this.#reading = true;
		try {
			this.#take(await this.#read());
		} catch {
			// The next ring asks again.
		} finally {
			this.#reading = false;
		}
		if (this.#again) {
			this.#again = false;
			await this.look();
		}
	}

	#take(read: FirstBenchmarkRead): void {
		const going = GOING.has(read.state);
		this.held = going ? (read.held ?? null) : null;
		if (this.#first) {
			this.#first = false;
			for (const key of keysOf(read)) this.#said.add(key);
		} else {
			for (const toast of benchmarkToasts(read, this.#said)) {
				this.#said.add(toast.key);
				if (!toast.going && this.#running !== null) {
					this.#unsay(this.#running);
					this.#running = null;
				}
				const id = this.#say(toast);
				if (toast.going) this.#running = id;
			}
		}
		/* A measured device with nothing going never queues one again: stop asking. */
		this.#finished = read.measured && !going;
	}
}

function readRun(): Promise<FirstBenchmarkRead> {
	return api.get<FirstBenchmarkRead>('/performance/benchmark');
}

/** Through the toaster. While it runs the toast stays (an unmeasured bar), as running work does. */
function showToast(toast: BenchmarkToast): number {
	return toasts.show(toast.message, {
		tone: toast.tone,
		progress: toast.going ? { value: null, max: 1 } : undefined,
		action: toast.press
			? {
					label: toast.press === 'review' ? COPY.measure.review : COPY.measure.open,
					run: () => openSettings('performance', MEASURE_ROW)
				}
			: undefined
	});
}

/**
 * The sentence for a folder just added whose files the benchmark holds back, or null where nothing
 * holds them: what the toast of whoever added it says in place of "Sift is reading".
 */
export async function heldFolder(): Promise<string | null> {
	try {
		const read = await readRun();
		return GOING.has(read.state) ? (read.held ?? null) : null;
	} catch {
		return null;
	}
}

/** The tab's one listener. */
export const benchmarkRun = new BenchmarkToasts();
