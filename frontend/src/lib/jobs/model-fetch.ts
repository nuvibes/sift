/*
 * A download followed from outside any one screen, and how it ended, in words and a toast.
 *
 * The models for faces, Smart Search and watermarks and GPU support download the same way, so one
 * that left nothing usable is said here once: the task's own reason when it failed, a cancel with
 * whether anything arrived, and otherwise that it ended without what it was for.
 */

import { api } from '$lib/api/client';
import { DownloadWatch } from '$lib/jobs/watch-download.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import type { components } from '$lib/api/schema';

export type Ending = Pick<components['schemas']['JobView'], 'state' | 'error' | 'progress'>;

/** The task's last row, or null when the queue no longer lists it. */
async function rowOf(type: string, jobId: string): Promise<Ending | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type, limit: 50 }
	});
	return page.jobs.find((one) => one.id === jobId) ?? null;
}

/** How a download that left `what` missing ended. The queue stores a failure behind the
 *  exception's name, which is not for this sentence. */
export function endedWithout(row: Ending | null, what = 'the models'): string {
	if (row?.state === 'failed' && row.error) return row.error.replace(/^[A-Z]\w*:\s*/, '');
	if (row?.state === 'canceled') {
		return row.progress > 0
			? 'Download canceled. What was downloaded is kept, so starting again costs only the rest.'
			: 'Download canceled before anything was downloaded.';
	}
	return `The download ended without ${what}. Open Activity to see why.`;
}

export class ModelFetchWatch extends DownloadWatch {
	/** The download last followed, kept past its end so its last row can be read. */
	#last = '';

	constructor(
		type: string,
		/** The sentence for what was fetched being usable, or null while it is not. */
		arrived: (row: Ending | null) => Promise<string | null>,
		what?: string
	) {
		super(type, async () => {
			const row = await rowOf(type, this.#last).catch(() => null);
			const usable = await arrived(row);
			const said = usable ?? endedWithout(row, what);
			// Every ending is announced, one that repeats the last too: that is how a press is seen.
			toasts.show(said, { tone: usable === null ? 'error' : 'success' });
			return said;
		});
	}

	override follow(jobId: string, state = 'queued'): void {
		this.#last = jobId;
		super.follow(jobId, state);
	}
}
