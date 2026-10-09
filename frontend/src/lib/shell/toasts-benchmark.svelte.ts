// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Says that Sift is benchmarking this device and what it set, in every admin window, from the jobs and
 * library bells; the first read only remembers. The sentence is the server's (`said`), as
 * Activity's.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { jobChanges, libraryChanges } from '$lib/library/changes.svelte';
import { COPY, MEASURE_ROW } from '$lib/settings-ui/Performance.search';
import { openSettings } from '$lib/settings-ui/settings-view';
import { toasts } from '$lib/shell/toasts.svelte';

export type FirstBenchmarkRead = Pick<
	components['schemas']['FirstBenchmarkView'],
	'state' | 'job_id' | 'said' | 'measured' | 'held'
>;

export interface BenchmarkToast {
	/** `<job id>:start` or `<job id>:end`, so each is said once. */
	key: string;
	message: string;
	tone: 'info' | 'success' | 'error';
	press: 'open' | 'review' | null;
	going: boolean;
}

const GOING = new Set(['waiting', 'running']);
const GAVE_WAY = 'gave_way';

/** THE RULE: running once, ended once; a device already benchmarked says nothing. */
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

function keysOf(read: FirstBenchmarkRead): string[] {
	if (!read.job_id) return [];
	const start = `${read.job_id}:start`;
	return GOING.has(read.state) ? [start] : [start, `${read.job_id}:end`];
}

/** One per tab; `read` and `say` are handed in for a test. */
export class BenchmarkToasts {
	/** The server's sentence for a folder held back while the run waits, kept on every read. */
	held = $state<string | null>(null);

	#said = new Set<string>();
	#first = true;
	#finished = false;
	#started = false;
	#reading = false;
	#again = false;
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

	follow(isAdmin: boolean): void {
		if (!isAdmin || this.#started) return;
		this.#started = true;
		jobChanges.subscribe(() => void this.look());
		libraryChanges.subscribe(() => void this.look());
		void this.look();
	}

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
		/* Measured and idle: it never queues again. */
		this.#finished = read.measured && !going;
	}
}

function readRun(): Promise<FirstBenchmarkRead> {
	return api.get<FirstBenchmarkRead>('/performance/benchmark');
}

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

export async function heldFolder(): Promise<string | null> {
	try {
		const read = await readRun();
		return GOING.has(read.state) ? (read.held ?? null) : null;
	} catch {
		return null;
	}
}

export const benchmarkRun = new BenchmarkToasts();
