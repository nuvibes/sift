import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The watermark models' download, once it ends: the sentence the Watermarks pane keeps after the
   bar goes. The watching itself is `jobs/watch-download`'s and is tested there. */

const asked = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock('$lib/api/client', () => ({
	// The queue no longer lists the job: the run has ended, however it ended.
	api: { get: vi.fn(async () => ({ jobs: [] })) },
	ApiError: class extends Error {}
}));
vi.mock('$lib/library/watermarks.svelte', () => ({
	FETCHING_MODELS: 'watermark_fetch_models',
	watermarkStatus: () => asked.status()
}));

const { modelFetch } = await import('./watermarks-runs.svelte');

async function ended(): Promise<void> {
	modelFetch.follow('j1');
	await vi.advanceTimersByTimeAsync(2000);
}

beforeEach(() => {
	vi.useFakeTimers();
	asked.status.mockReset();
});

afterEach(() => vi.useRealTimers());

describe("the watermark models' download, once it ends", () => {
	it('says watermarks can be read when the server says the models are here', async () => {
		asked.status.mockResolvedValue({ ready: true });

		await ended();

		expect(modelFetch.running).toBe(false);
		expect(modelFetch.outcome).toBe(
			'The models are on this machine. Sift can read watermarks now.'
		);
	});

	it('says it stopped short when they are not, or when the server could not be asked', async () => {
		asked.status.mockResolvedValue({ ready: false });
		await ended();
		expect(modelFetch.outcome).toContain('The download stopped before it finished');

		asked.status.mockRejectedValue(new Error('offline'));
		await ended();
		expect(modelFetch.outcome).toContain('The download stopped before it finished');
	});
});
