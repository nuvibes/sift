/* The sweep watcher, which has to survive the screen that started it.
 *
 * Held on a settings pane, the bar and the count would vanish the moment somebody clicked away
 * and come back to nothing when they returned, while the scanning carried on the whole time.
 * The screen would really be reporting that it had stopped looking.
 *
 * So what is under test is picking a run up rather than drawing one: that a sweep still queueing
 * is found, that a sweep whose queueing has finished but whose scans are still going is ALSO
 * found (the state somebody reopening the screen is most likely to arrive in), that a finished
 * run is not, and that the run is identified by the job it started from rather than by whichever
 * page happens to be newest.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { describeWait, sweep, troubleThisRun } from '$lib/people/faces.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {
		status: number;
		constructor(status: number) {
			super('failed');
			this.status = status;
		}
	}
}));

const mocked = vi.mocked(api);

/* One page of a sweep. `parent` is what makes the chain a chain: the first page has none, and each
 * later one hangs off the page that queued it. */
function sweepPage(id: string, state: string, at: number, parent: string | null = null) {
	return { id, parent_id: parent, state, created_at: at, note: null };
}

/* The two routes `resume` reads, answered by what each query asks for. Written as one router
 * rather than a queue of replies, because the order the calls go out in is the implementation's
 * business and a test that pins it fails on a refactor that changed nothing. */
function answering(options: { sweeps: ReturnType<typeof sweepPage>[]; scansWaiting?: number }) {
	mocked.get.mockImplementation((path: string, init?: { query?: Record<string, unknown> }) => {
		const query = init?.query ?? {};
		if (query.type === 'face_sweep') return Promise.resolve({ jobs: options.sweeps });
		if (query.type === 'face_scan') {
			// Split across the two states the caller sums, so the total is what it asked for.
			const waiting = options.scansWaiting ?? 0;
			return Promise.resolve({ total: query.state === 'queued' ? waiting : 0 });
		}
		return Promise.resolve({ jobs: [], total: 0 });
	});
}

describe('picking up a sweep that is already running', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		sweep.stopped();
	});

	it('follows a run whose pages are still queueing', async () => {
		answering({
			sweeps: [
				sweepPage('page-2', 'running', 200, 'page-1'),
				sweepPage('page-1', 'done', 100, null)
			]
		});

		await sweep.resume();

		expect(sweep.jobId).toBe('page-1');
	});

	it('follows a run that has finished queueing while its scans carry on', async () => {
		/* The state somebody reopening the screen is most likely to arrive in, and the easy one
		 * to get wrong: no sweep job is working, so "is a sweep running" reads as no, and the
		 * hours of actual scanning would have nothing on screen at all.
		 */
		answering({
			sweeps: [sweepPage('page-2', 'done', 200, 'page-1'), sweepPage('page-1', 'done', 100, null)],
			scansWaiting: 412
		});

		await sweep.resume();

		expect(sweep.jobId).toBe('page-1');
	});

	it('follows nothing when the run is over and nothing is left to scan', async () => {
		answering({
			sweeps: [sweepPage('page-1', 'done', 100, null)],
			scansWaiting: 0
		});

		await sweep.resume();

		expect(sweep.jobId).toBeNull();
	});

	it('follows nothing when there has never been a sweep', async () => {
		answering({ sweeps: [] });

		await sweep.resume();

		expect(sweep.jobId).toBeNull();
	});

	it('does not walk out of the run when a page id loops back on itself', async () => {
		/* A guard rather than a scenario: a job whose parent chain cycles would spin forever here,
		 * in a function called on every mount. */
		answering({
			sweeps: [
				sweepPage('page-a', 'running', 200, 'page-b'),
				sweepPage('page-b', 'done', 100, 'page-a')
			]
		});

		await sweep.resume();

		expect(sweep.jobId).not.toBeNull();
	});
});

describe('saying roughly how long is left', () => {
	it('rounds to what somebody would plan around', () => {
		/* Deliberately vague. The number behind it is a rate over the last minute and the files
		 * ahead are not the files behind, so "19 minutes 4 seconds" is the same guess wearing three
		 * digits of invented precision, and it is worse for being precise, because it reads as a
		 * promise. */
		expect(describeWait(20)).toBe('under a minute left');
		expect(describeWait(59)).toBe('under a minute left');
		expect(describeWait(60)).toBe('a few minutes left');
		expect(describeWait(1144)).toBe('about 15 to 20 minutes left');
		expect(describeWait(7200)).toBe('about 2 hours left');
		expect(describeWait(20_000)).toBe('about 5 to 6 hours left');
	});

	it('never says nothing is left while files are still queued', () => {
		/* A run down to its last file is still running, and "0 minutes left" beside a bar that is
		 * not full reads as a fault. */
		expect(describeWait(0)).toBe('under a minute left');
	});
});

describe('what a failure says', () => {
	it('drops the exception class the queue records in front of the message', async () => {
		/* The queue stores the class name and the message, which is what an admin reading the jobs
		 * dashboard wants. Here it is the sentence somebody is being asked to act on, and opening it
		 * by naming a Python class at somebody whose library has no faces in it explains nothing. */
		mocked.get.mockImplementation((path: string, init?: { query?: Record<string, unknown> }) => {
			const query = init?.query ?? {};
			if (query.state === 'failed' && query.limit === 1 && query.parent_id === undefined) {
				return Promise.resolve({
					jobs: [{ error: 'DeviceUnavailable: Recognition was set to use a graphics card.' }]
				});
			}
			return Promise.resolve({ total: 3, jobs: [] });
		});

		const trouble = await troubleThisRun(['page-1']);

		expect(trouble.failed).toBe(3);
		expect(trouble.reason).toBe('Recognition was set to use a graphics card.');
	});
});

/*
 * What a run that was switched off halfway says when it ends.
 *
 * The bar itself is sound: it is drawn from how many scans are QUEUED OR RUNNING, which is the
 * queue and not a count of files left to do, so it drains to nothing and the watcher stops. The
 * sentence at the end is the risk. Switching recognition off does not cancel what is already
 * queued; every one of those scans runs, checks the switch, finds it off, and finishes having done
 * nothing. The queue empties exactly as it would after a real run, and a toast saying the library
 * had been gone through would be false.
 */
describe('what is said when a run ends', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		sweep.stopped();
	});

	/** Follow a run, let one tick pass, and hand back whatever was announced.
	 *
	 *  `queued` is how many files the run really put through, which is what the server's own count
	 *  of the run's children answers. Zero is a run that found nothing needing a look. */
	async function ranToTheEnd(options: {
		enabled: boolean;
		note?: string | null;
		queued?: number;
	}): Promise<string | null> {
		const page = {
			id: 'run-1',
			parent_id: null,
			state: 'done',
			created_at: 10,
			note: options.note ?? null
		};
		mocked.get.mockImplementation((path: string, init?: { query?: Record<string, unknown> }) => {
			if (path === '/faces/settings') return Promise.resolve({ enabled: options.enabled });
			const query = init?.query ?? {};
			if (query.type === 'face_sweep') return Promise.resolve({ jobs: [page] });
			// The run's children: how many files it queued. `failed` is asked for separately and is
			// none of them here.
			if (query.type === 'face_scan' && query.state === undefined) {
				return Promise.resolve({ jobs: [], total: options.queued ?? 0 });
			}
			return Promise.resolve({ jobs: [], total: 0 });
		});

		let said: string | null = null;
		sweep.announceWith((message) => {
			said = message;
		});
		vi.useFakeTimers();
		try {
			sweep.follow('run-1');
			await vi.advanceTimersByTimeAsync(3000);
		} finally {
			vi.useRealTimers();
		}
		return said;
	}

	it('says the library was gone through when recognition is still on', async () => {
		expect(await ranToTheEnd({ enabled: true })).toBe(
			'Sift has finished going through the library.'
		);
	});

	it('says what the run DID, not what it was about to do', async () => {
		/*
		 * Not the sweep's own note, which is about QUEUEING and is written the moment the walk over
		 * the library finishes, while every one of those scans is still to run. Read out at the
		 * other end, once the queue has drained, it would announce forty files as about to be
		 * scanned at the moment the last of them had been scanned.
		 *
		 * The note itself is not wrong; it is the right sentence on the Jobs screen, beside a sweep
		 * that is still queueing. It is the wrong sentence here.
		 */
		const said = await ranToTheEnd({
			enabled: true,
			note: '40 files queued to scan.',
			queued: 40
		});

		expect(said).not.toMatch(/queued/i);
		expect(said).toBe('Sift has finished going through the library. 40 files were looked at.');
	});

	it('counts one file as one file', async () => {
		expect(await ranToTheEnd({ enabled: true, note: '1 file queued to scan.', queued: 1 })).toBe(
			'Sift has finished going through the library. 1 file was looked at.'
		);
	});

	it('lets the sweep speak for a run that found nothing to do', async () => {
		/* The one case where the sweep's own sentence is the right one out here: it is already in
		   the past tense, and it names the reason (the settings), which nothing at this end
		   knows. */
		const said = await ranToTheEnd({
			enabled: true,
			note: 'Everything has already been scanned under these settings.',
			queued: 0
		});

		expect(said).toBe('Everything has already been scanned under these settings.');
	});

	it('says the queue was skipped when the switch went off underneath it', async () => {
		/* The claim somebody acts on. An empty queue is not the same as a library that has been
		   looked at, and from out here the two are identical. */
		const said = await ranToTheEnd({ enabled: false });

		expect(said).toMatch(/switched off/i);
		expect(said).not.toMatch(/finished going through the library/i);
		expect(said).toMatch(/nothing found so far has been lost/i);
	});

	it('still announces the ending when the settings answer cannot be read', async () => {
		/* A settings read that fails must not turn a completed run into a warning about a switch
		   nobody touched, and it must not swallow the ending either. Silence on that question means
		   carry on. */
		mocked.get.mockImplementation((path: string, init?: { query?: Record<string, unknown> }) => {
			if (path === '/faces/settings') return Promise.reject(new Error('offline'));
			const query = init?.query ?? {};
			if (query.type === 'face_sweep')
				return Promise.resolve({
					jobs: [
						{
							id: 'run-1',
							parent_id: null,
							state: 'done',
							created_at: 10,
							note: '40 files queued to scan.'
						}
					]
				});
			if (query.type === 'face_scan' && query.state === undefined)
				return Promise.resolve({ jobs: [], total: 40 });
			return Promise.resolve({ jobs: [], total: 0 });
		});

		let said: string | null = null;
		sweep.announceWith((message) => {
			said = message;
		});
		vi.useFakeTimers();
		try {
			sweep.follow('run-1');
			await vi.advanceTimersByTimeAsync(3000);
		} finally {
			vi.useRealTimers();
		}

		expect(said).toBe('Sift has finished going through the library. 40 files were looked at.');
	});
});
