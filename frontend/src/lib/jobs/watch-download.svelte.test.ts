/*
 * Waiting for a worker is not downloading.
 *
 * A download is a job like any other, so on a busy machine it sits on the queue until something
 * picks it up: minutes, behind hundreds of jobs. Treating any job that has not ended as one in
 * flight would have the Faces pane draw "0% - downloading" for the whole of that wait: a percentage
 * that is not moving, for a download that has not started, which reads as a stuck download rather
 * than a queue.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { DownloadWatch, sayWaiting } from './watch-download.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

const KIND = 'fetch_weights';

function job(state: string, progress = 0, note: string | null = null) {
	return { id: 'j1', state, progress, note };
}

/** The queue's answer for this kind, as the listing route shapes it. */
function page(...jobs: ReturnType<typeof job>[]) {
	return { jobs, total: jobs.length, counts: {}, by_type: {}, work: {}, families: {} };
}

function watcher(): DownloadWatch {
	return new DownloadWatch(KIND, async () => 'It landed.');
}

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
});

/* Back to the real clock before the suite's own teardown, which waits on a real timer: fake ones
   left in place hang it rather than failing anything, so every test passes and the file does not. */
afterEach(() => vi.useRealTimers());

describe('a download nothing has picked up', () => {
	it('is joined, and says it is waiting rather than downloading', async () => {
		mocked.get.mockResolvedValue(page(job('queued')) as never);

		const watch = watcher();
		await watch.resume();

		// Joined: the screen must not offer the button again, or somebody starts a second one.
		expect(watch.running).toBe(true);
		expect(watch.waiting).toBe(true);
		expect(watch.status).toBe('Waiting to start');
		expect(watch.status).not.toContain('downloading');
	});

	it('and so is one just started here, before anything has been asked', () => {
		const watch = watcher();
		watch.follow('j1');

		expect(watch.waiting).toBe(true);
		expect(watch.status).toBe('Waiting to start');
	});

	it('and a job waiting on something else is waiting too', async () => {
		mocked.get.mockResolvedValue(page(job('blocked')) as never);

		const watch = watcher();
		await watch.resume();

		expect(watch.status).toBe('Waiting to start');
	});
});

describe('a download a worker has taken', () => {
	it('says how far it has got, in the words the job itself uses', async () => {
		mocked.get.mockResolvedValue(page(job('running', 0.42, 'the detector')) as never);

		const watch = watcher();
		watch.follow('j1');
		expect(watch.waiting).toBe(true);

		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.waiting).toBe(false);
		expect(watch.status).toBe('42% — the detector');
	});

	it('and falls back to the plain word when the job says nothing', async () => {
		mocked.get.mockResolvedValue(page(job('running', 0.5)) as never);

		const watch = watcher();
		watch.follow('j1');
		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.status).toBe('50% — downloading');
	});

	it('and says it is finishing up once the bytes are all in', async () => {
		mocked.get.mockResolvedValue(page(job('running', 1)) as never);

		const watch = watcher();
		watch.follow('j1');
		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.status).toBe('100% — finishing up');
	});
});

describe('the end of one', () => {
	it('stops being followed and keeps the outcome', async () => {
		mocked.get.mockResolvedValue(page(job('done', 1)) as never);

		const watch = watcher();
		watch.follow('j1');
		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.running).toBe(false);
		expect(watch.waiting).toBe(false);
		expect(watch.outcome).toBe('It landed.');
	});

	it('and a finished job is not one to join', async () => {
		mocked.get.mockResolvedValue(page(job('failed', 0.3)) as never);

		const watch = watcher();
		await watch.resume();

		expect(watch.running).toBe(false);
		expect(watch.waiting).toBe(false);
	});
});

describe('where it is in the line', () => {
	it('says what is ahead of it, and names being first rather than counting nought', () => {
		/* A page of fifty rows and a tally by state are not a position, so the number comes from the
		 * server, which counts the line in the order the queue is really claimed in.
		 *
		 * One-based off the wire; the words say what is AHEAD, which is the question somebody
		 * watching a bar asks. "0 ahead" in a sentence about waiting reads as a fault, so first in
		 * line is named. */
		expect(sayWaiting(1)).toBe('Next to start');
		expect(sayWaiting(2)).toBe('Waiting to start — 1 ahead');
		expect(sayWaiting(581)).toBe('Waiting to start — 580 ahead');
	});

	it('falls back to the plain sentence where there is no place to give', () => {
		/* A `blocked` job is waiting on something rather than on a free worker, so it has no place
		 * in the line at all, and a build whose server cannot answer loses the number and nothing
		 * else. */
		expect(sayWaiting(null)).toBe('Waiting to start');
	});
});
