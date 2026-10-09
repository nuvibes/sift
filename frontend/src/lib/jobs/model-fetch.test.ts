import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Ending } from './model-fetch';

/* How a model download that left no models behind ended, said from the task's own row. */

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('$lib/api/client', () => ({ api: { get: apiGet }, ApiError: class extends Error {} }));

const { ModelFetchWatch, endedWithout } = await import('./model-fetch');
const { toasts } = await import('$lib/shell/toasts.svelte');

const arrived = vi.fn<(row: Ending | null) => Promise<string | null>>();
const watch = new ModelFetchWatch('probe_fetch_models', (row) => arrived(row));

/** The queue's answer once the download has ended: its own row, in the state it ended in. */
function endsAs(row: { state: string; error?: string | null; progress?: number } | null): void {
	const jobs = row ? [{ id: 'j1', error: null, progress: 0, note: null, ...row }] : [];
	apiGet.mockResolvedValue({ jobs });
}

async function ended(): Promise<void> {
	watch.follow('j1');
	await vi.advanceTimersByTimeAsync(2000);
}

beforeEach(() => {
	vi.useFakeTimers();
	apiGet.mockReset();
	arrived.mockReset();
	arrived.mockResolvedValue(null);
});

afterEach(() => vi.useRealTimers());

describe('a download that ended without the models', () => {
	it("says the task's own reason, without the name the queue files it under", async () => {
		endsAs({
			state: 'failed',
			error:
				"WeightError: The detector model couldn't be downloaded: the connection to models.example.test was refused."
		});

		await ended();

		expect(watch.outcome).toBe(
			"The detector model couldn't be downloaded: the connection to models.example.test was refused."
		);
	});

	it('says what arrived is kept only when something did', async () => {
		endsAs({ state: 'canceled', progress: 0.4 });
		await ended();
		expect(watch.outcome).toMatch(/^Download canceled\. What was downloaded is kept/);

		endsAs({ state: 'canceled', progress: 0 });
		await ended();
		expect(watch.outcome).toBe('Download canceled before anything was downloaded.');
	});

	it('says it ended without them when the row says no more, or is gone', async () => {
		endsAs({ state: 'failed', error: null });
		await ended();
		expect(watch.outcome).toBe('The download ended without the models. Open Activity to see why.');

		endsAs(null);
		await ended();
		expect(watch.outcome).toBe('The download ended without the models. Open Activity to see why.');

		// The row can't be read a second time: said the same way, never as success.
		apiGet
			.mockResolvedValueOnce({ jobs: [{ id: 'j1', state: 'failed', progress: 0, note: null }] })
			.mockRejectedValueOnce(new Error('offline'));
		await ended();
		expect(watch.outcome).toBe('The download ended without the models. Open Activity to see why.');
	});

	it('says the sentence for what arrived, told from the row where the caller wants it', async () => {
		arrived.mockImplementation(async (row) => (row?.state === 'done' ? 'Here now.' : null));
		endsAs({ state: 'done' });

		await ended();

		expect(watch.outcome).toBe('Here now.');
	});

	it('announces every ending, one that repeats the last too, and its tone', async () => {
		const shown = vi.spyOn(toasts, 'show');
		endsAs({ state: 'canceled', progress: 0 });
		await ended();
		await ended();
		arrived.mockResolvedValue('Here now.');
		await ended();

		expect(shown.mock.calls).toEqual([
			['Download canceled before anything was downloaded.', { tone: 'error' }],
			['Download canceled before anything was downloaded.', { tone: 'error' }],
			['Here now.', { tone: 'success' }]
		]);
		shown.mockRestore();
	});

	it('names what it was for', () => {
		expect(endedWithout(null)).toMatch(/ended without the models/);
		expect(endedWithout(null, 'GPU support')).toBe(
			'The download ended without GPU support. Open Activity to see why.'
		);
	});
});
