import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The download's end, as the Faces pane and the first run both read it. The watching machinery is
   `jobs/watch-download`'s and has its own tests; what is here is the part about faces: the sentence
   at the end, and the fresh answer about whether recognition can run now. */

const asked = vi.hoisted(() => ({ settings: vi.fn() }));
vi.mock('$lib/api/client', () => ({
	// The queue does not list the job: the run has ended, however it ended.
	api: { get: vi.fn(async () => ({ jobs: [] })) },
	ApiError: class extends Error {}
}));
vi.mock('$lib/people/faces.svelte', () => ({
	FETCHING_WEIGHTS: 'face_fetch_weights',
	faceSettings: () => asked.settings()
}));

const { modelFetch } = await import('./faces-runs.svelte');

async function ended(): Promise<void> {
	modelFetch.follow('j1');
	await vi.advanceTimersByTimeAsync(2000);
}

beforeEach(() => {
	vi.useFakeTimers();
	asked.settings.mockReset();
});

afterEach(() => vi.useRealTimers());

describe("recognition's model download, once it ends", () => {
	it('says recognition can run, and keeps what the server said for the pane to draw', async () => {
		asked.settings.mockResolvedValue({ ready: true });

		await ended();

		expect(modelFetch.running).toBe(false);
		expect(modelFetch.outcome).toBe('The models are installed. Sift can recognize faces now.');
		expect(modelFetch.settled).toEqual({ ready: true });
	});

	it('says it ended without them when the models are still not all there', async () => {
		asked.settings.mockResolvedValue({ ready: false });

		await ended();

		expect(modelFetch.outcome).toContain('The download ended without the models');
	});

	it('never claims success when the server could not be asked', async () => {
		asked.settings.mockRejectedValue(new Error('offline'));

		await ended();

		expect(modelFetch.outcome).toContain('The download ended without the models');
		expect(modelFetch.settled).toBeNull();
	});
});
