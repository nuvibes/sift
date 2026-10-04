/* The two long-running things searching by meaning does, followed from outside any one screen.
 *
 * The pane's own tests assert markup and nothing about what is behind it, so the estimate, the
 * resume, and the whole of the "switched off mid-run" stop are pinned here.
 *
 * The one worth stating plainly is `running`. It is asked of the QUEUE and not of the file
 * counts. "Files still to do" is not the question: a run stopped halfway leaves exactly as many
 * files undone as a run still going, so a bar drawn from that number would sit at whatever it
 * reached, for ever, describing work that will never happen. Switching the feature off does not
 * cancel the queued jobs. It makes each one run, find the switch off, and finish having done
 * nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SemanticStatus } from '$lib/search/semantic.svelte';

const semanticStatus = vi.hoisted(() => vi.fn());
const semanticAvailable = vi.hoisted(() => vi.fn());
const semanticCoverage = vi.hoisted(() => vi.fn());
const apiGet = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', () => ({
	api: { get: apiGet },
	ApiError: class extends Error {}
}));

vi.mock('$lib/search/semantic.svelte', () => ({
	FETCHING_MODELS: 'semantic_fetch_models',
	semanticStatus,
	semanticAvailable,
	semanticCoverage
}));

vi.mock('$lib/jobs/queue.svelte', () => ({
	isFinished: (state: string) => ['done', 'failed', 'canceled'].includes(state)
}));

const { availability, coverage, describing, modelFetch } =
	await import('$lib/jobs/semantic-runs.svelte');

/** A status with everything answered, so a test only says the part it is about. */
function status(overrides: Partial<SemanticStatus> = {}): SemanticStatus {
	return {
		supported: true,
		enabled: true,
		ready: true,
		family: 'compact',
		device: 'cpu',
		indexed_frames: 0,
		described_files: 0,
		waiting_files: 0,
		running_jobs: 0,
		described_by_another_model: 0,
		problem: null,
		installed: [],
		...overrides
	};
}

/* Start reading, and let the first reading land.
 *
 * `attach` awaits its own polling loop, which does not return while a run is still going, so a
 * test that awaited it would hang exactly where the interesting cases are. The pane calls it the
 * same way, without waiting, for the same reason.
 */
async function attach() {
	void describing.attach();
	await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
	vi.useFakeTimers();
	semanticStatus.mockReset();
	semanticAvailable.mockReset();
	semanticCoverage.mockReset();
	apiGet.mockReset();
});

afterEach(() => {
	describing.detach();
	describing.status = null;
	describing.outcome = null;
	modelFetch.couldNotStart('');
	modelFetch.outcome = null;
	vi.useRealTimers();
});

/* --- describing the library ------------------------------------------------------------- */

describe('whether anything is actually describing', () => {
	it('says nothing is running before the first reading lands', () => {
		expect(describing.running).toBe(false);
		expect(describing.stopped).toBe(false);
	});

	it('follows the queue rather than the count of files left', async () => {
		describing.status = status({ described_files: 10, waiting_files: 90, running_jobs: 3 });

		expect(describing.running).toBe(true);
		expect(describing.stopped).toBe(false);
	});

	it('a run that stopped halfway is stopped, not running', async () => {
		/* The exact state switching the feature off mid-run leaves behind: work left, nothing doing
		   it. Identical to a run in progress from the file counts alone, and the opposite answer. */
		describing.status = status({ described_files: 10, waiting_files: 90, running_jobs: 0 });

		expect(describing.running).toBe(false);
		expect(describing.stopped).toBe(true);
	});

	it('a library with nothing left is neither running nor stopped', () => {
		describing.status = status({ described_files: 100, waiting_files: 0, running_jobs: 0 });

		expect(describing.running).toBe(false);
		expect(describing.stopped).toBe(false);
	});

	it('a feature that cannot run is neither, whatever the counts say', () => {
		/* Switched on with no models is an ordinary state. Drawing a stopped-run warning over it
		   would tell somebody to carry on describing when nothing has begun. */
		describing.status = status({ ready: false, waiting_files: 90, running_jobs: 0 });

		expect(describing.running).toBe(false);
		expect(describing.stopped).toBe(false);
	});
});

describe('how far through it is', () => {
	it('is null on an install where nothing has been described and nothing is waiting', () => {
		/* Not "0% done": not started. A bar at zero on a fresh install reads as stuck. */
		describing.status = status();

		expect(describing.fraction).toBeNull();
	});

	it('is the share of the whole once there is something to divide by', () => {
		describing.status = status({ described_files: 25, waiting_files: 75 });

		expect(describing.fraction).toBeCloseTo(0.25);
	});
});

describe('reading a run while it goes', () => {
	it('picks up a run already going, so opening the pane joins it', async () => {
		semanticStatus.mockResolvedValue(
			status({ described_files: 5, waiting_files: 5, running_jobs: 2 })
		);

		await attach();

		expect(describing.done).toBe(5);
		expect(describing.left).toBe(5);
		expect(describing.running).toBe(true);
	});

	it('reads the queue afresh once a Build has been asked for here', async () => {
		semanticStatus.mockResolvedValue(status({ waiting_files: 5, running_jobs: 1 }));
		describing.follow();
		await vi.advanceTimersByTimeAsync(2100);

		expect(describing.running).toBe(true);
	});

	it('stops and says so when the switch goes off underneath it', async () => {
		semanticStatus.mockResolvedValue(status({ ready: false, waiting_files: 40 }));
		describing.follow();
		await vi.advanceTimersByTimeAsync(2100);

		expect(describing.running).toBe(false);
		expect(describing.outcome).toMatch(/nothing described so far has been lost/i);
	});

	it('says how many were left when a run stops with work outstanding', async () => {
		semanticStatus.mockResolvedValue(status({ described_files: 3, waiting_files: 17 }));

		await attach();

		expect(describing.outcome).toContain('17');
		expect(describing.outcome).toMatch(/picks up where it left off/i);
	});

	it('says everything is described when there is nothing left and something was done', async () => {
		semanticStatus.mockResolvedValue(status({ described_files: 12, waiting_files: 0 }));

		await attach();

		expect(describing.outcome).toMatch(/everything sift can see has been described/i);
	});

	it('says nothing at all on an install where nothing has ever been described', async () => {
		semanticStatus.mockResolvedValue(status());

		await attach();

		expect(describing.outcome).toBeNull();
	});

	it('a reading that cannot be taken ends the watch rather than clearing the screen', async () => {
		semanticStatus.mockRejectedValue(new Error('offline'));

		await attach();

		expect(describing.status).toBeNull();
	});

	it('stops reading once the pane goes', async () => {
		semanticStatus.mockResolvedValue(status({ waiting_files: 5, running_jobs: 1 }));
		await attach();
		const readings = semanticStatus.mock.calls.length;

		describing.detach();
		await vi.advanceTimersByTimeAsync(6000);

		expect(semanticStatus.mock.calls.length).toBe(readings);
	});
});

describe('roughly how long is left', () => {
	it('says nothing until there is enough history to divide by', async () => {
		semanticStatus.mockResolvedValue(
			status({ described_files: 0, waiting_files: 100, running_jobs: 1 })
		);
		await attach();

		expect(describing.remaining).toBeNull();
	});

	it('works it out from what the count has done lately', async () => {
		/* Ten files a reading, two seconds a reading, ninety left after the window: about eighteen
		   seconds. What matters is that it is a number at all: a getter that returns before reading
		   anything reactive leaves the estimate silently null for a whole run. */
		let left = 100;
		semanticStatus.mockImplementation(async () =>
			status({ described_files: 200 - left, waiting_files: left, running_jobs: 1 })
		);
		await attach();
		for (let tick = 0; tick < 8; tick += 1) {
			left -= 10;
			await vi.advanceTimersByTimeAsync(2100);
		}

		expect(describing.remaining).not.toBeNull();
		expect(describing.remaining!).toBeGreaterThan(0);
	});

	it('says nothing when the count has not moved across the whole window', async () => {
		/* What a stalled run looks like. No number is better than one that ticks up by a second
		   every second. */
		semanticStatus.mockResolvedValue(
			status({ described_files: 5, waiting_files: 95, running_jobs: 1 })
		);
		await attach();
		for (let tick = 0; tick < 8; tick += 1) await vi.advanceTimersByTimeAsync(2100);

		expect(describing.remaining).toBeNull();
	});
});

/* --- fetching the models ---------------------------------------------------------------- */

/* The progress of a download is read off the jobs list, which is one `api.get`. The watcher is
   shared with the graphics-card download and lives in `$lib/jobs/watch-download`, so these drive
   the request itself. */
function theJobSays(row: { state: string; progress?: number; note?: string | null }): void {
	apiGet.mockResolvedValue({ jobs: [{ id: 'job-1', progress: 0, note: null, ...row }] });
}

describe('following a model download', () => {
	it('is not running until one is', () => {
		expect(modelFetch.running).toBe(false);
	});

	it('carries the job note, which is what says which file of the set this is', async () => {
		theJobSays({ state: 'running', progress: 0.4, note: '2 of 3 - word reader' });
		modelFetch.follow('job-1');
		await vi.advanceTimersByTimeAsync(2100);

		expect(modelFetch.fraction).toBeCloseTo(0.4);
		expect(modelFetch.note).toBe('2 of 3 - word reader');
	});

	it('re-reads whether the install can search by meaning once the job is over', async () => {
		/* Deliberately re-read rather than deduced from the job's last state: finishing, failing and
		   being cancelled all ask the same question, and the status is where it is answered. */
		apiGet.mockResolvedValue({ jobs: [] });
		semanticStatus.mockResolvedValue(status({ ready: true }));
		semanticAvailable.mockResolvedValue({ available: true });
		modelFetch.follow('job-1');
		await vi.advanceTimersByTimeAsync(2100);

		expect(modelFetch.running).toBe(false);
		expect(modelFetch.outcome).toMatch(/sift can search by meaning now/i);
		expect(availability.available).toBe(true);
	});

	it('says what arrived is kept when a download stops before it finishes', async () => {
		theJobSays({ state: 'failed', progress: 0.3 });
		semanticStatus.mockResolvedValue(status({ ready: false }));
		semanticAvailable.mockResolvedValue({ available: false });
		modelFetch.follow('job-1');
		await vi.advanceTimersByTimeAsync(2100);

		expect(modelFetch.outcome).toMatch(/starting again costs only the rest/i);
	});

	it('says so when the run can no longer be followed at all', async () => {
		apiGet.mockRejectedValue(new Error('offline'));
		modelFetch.follow('job-1');
		await vi.advanceTimersByTimeAsync(2100);

		expect(modelFetch.running).toBe(false);
		expect(modelFetch.outcome).toMatch(/open activity/i);
	});

	it('picks up a download already going when a screen opens', async () => {
		apiGet.mockResolvedValue({
			jobs: [
				{ id: 'old', state: 'done' },
				{ id: 'live', state: 'running' }
			]
		});

		await modelFetch.resume();

		expect(modelFetch.running).toBe(true);
	});

	it('joins one already being followed rather than starting a second watcher', async () => {
		theJobSays({ state: 'running', progress: 0.1 });
		apiGet.mockClear();
		modelFetch.follow('job-1');

		await modelFetch.resume();

		expect(apiGet).not.toHaveBeenCalled();
	});

	it('follows nothing when every download is over', async () => {
		apiGet.mockResolvedValue({ jobs: [{ id: 'old', state: 'done' }] });

		await modelFetch.resume();

		expect(modelFetch.running).toBe(false);
	});

	it('a failure to start is said rather than shown as a run', () => {
		modelFetch.couldNotStart('The models could not be fetched.');

		expect(modelFetch.running).toBe(false);
		expect(modelFetch.outcome).toBe('The models could not be fetched.');
	});
});

/* --- whether the box offers to search that way ------------------------------------------- */

describe('whether the search box should offer it', () => {
	it('reads the answer again on demand, which is what a switch elsewhere does', async () => {
		semanticAvailable.mockResolvedValue({ available: true });
		await availability.refresh();
		expect(availability.available).toBe(true);

		semanticAvailable.mockResolvedValue({ available: false });
		await availability.refresh();
		expect(availability.available).toBe(false);
	});

	it('TAKES the control away when the install stops being able to answer', async () => {
		/* Nobody signed in yet, or the machine cannot say. Either way the safe answer is no: an
		   offered control that does nothing is worse than an absent one.
		 *
		 * Turned ON first, deliberately. Starting from off, a refusal that did nothing at all would
		 * leave it off and the assertion would pass against a store that had stopped failing safe. */
		semanticAvailable.mockResolvedValue({ available: true });
		await availability.refresh();
		expect(availability.available).toBe(true);

		semanticAvailable.mockRejectedValue(new Error('401'));
		await availability.refresh();

		expect(availability.available).toBe(false);
	});

	it('asks once per page load however many screens ask for it', async () => {
		semanticAvailable.mockResolvedValue({ available: true });

		await availability.load();
		await availability.load();
		await availability.load();

		expect(semanticAvailable).toHaveBeenCalledTimes(1);
	});
});

describe('how much of the library a search by meaning can reach', () => {
	it('keeps both counts, so the sentence can say one out of the other', async () => {
		semanticCoverage.mockResolvedValue({ described: 17581, library: 101397 });

		await coverage.load();

		expect(coverage.described).toBe(17581);
		expect(coverage.library).toBe(101397);
	});

	it('says nothing at all rather than a fraction it could not read', async () => {
		/* Zero and zero is how the line disappears. A sentence built out of a failed read would be
		   a number somebody could act on that nothing stands behind. */
		semanticCoverage.mockResolvedValue({ described: 17581, library: 101397 });
		await coverage.load();

		semanticCoverage.mockRejectedValue(new Error('401'));
		await coverage.load();

		expect(coverage.described).toBe(0);
		expect(coverage.library).toBe(0);
	});

	it('shares one request between walls asking at the same moment', async () => {
		/* Two walls drawing the same search must not be two counts over the library. */
		semanticCoverage.mockResolvedValue({ described: 1, library: 2 });

		await Promise.all([coverage.load(), coverage.load(), coverage.load()]);

		expect(semanticCoverage).toHaveBeenCalledTimes(1);
	});
});
