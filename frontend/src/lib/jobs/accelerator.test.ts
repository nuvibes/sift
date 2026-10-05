import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The GPU support download, once it ends: the sentence the GPU pane keeps after the bar goes. */

const apiGet = vi.hoisted(() => vi.fn());
vi.mock('$lib/api/client', () => ({ api: { get: apiGet }, ApiError: class extends Error {} }));

const { accelWatch } = await import('./accelerator.svelte');

function endsAs(row: { state: string; error?: string | null; progress?: number }): void {
	apiGet.mockResolvedValue({ jobs: [{ id: 'g1', error: null, progress: 0, note: null, ...row }] });
}

async function ended(): Promise<void> {
	accelWatch.follow('g1');
	await vi.advanceTimersByTimeAsync(2000);
}

beforeEach(() => {
	vi.useFakeTimers();
	apiGet.mockReset();
});

afterEach(() => vi.useRealTimers());

describe('the GPU support download, once it ends', () => {
	it('says it finished, and leaves whether the GPU works to the test', async () => {
		endsAs({ state: 'done', progress: 1 });
		await ended();
		expect(accelWatch.outcome).toBe('The download finished. Test the GPU to see that it works.');
	});

	it("says the task's own reason when it failed, and claims nothing arrived", async () => {
		endsAs({
			state: 'failed',
			error:
				"AccelError: The onnxruntime package couldn't be downloaded: files.example.test didn't answer in time. Check the internet connection, then try again."
		});
		await ended();
		expect(accelWatch.outcome).toBe(
			"The onnxruntime package couldn't be downloaded: files.example.test didn't answer in time. Check the internet connection, then try again."
		);
	});

	it('says what arrived is kept only when a cancel came after something did', async () => {
		endsAs({ state: 'canceled', progress: 0.2 });
		await ended();
		expect(accelWatch.outcome).toMatch(/^Download canceled\. What arrived is kept/);

		endsAs({ state: 'canceled', progress: 0 });
		await ended();
		expect(accelWatch.outcome).toBe('Download canceled before anything arrived.');
	});

	it('names GPU support when the row says nothing more', async () => {
		apiGet.mockResolvedValue({ jobs: [] });
		await ended();
		expect(accelWatch.outcome).toBe(
			'The download ended without GPU support. Open Activity to see why.'
		);
	});
});
