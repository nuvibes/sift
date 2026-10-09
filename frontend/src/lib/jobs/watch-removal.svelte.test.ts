/* Delete face data and Delete index run as jobs: the press is held while the job runs, and the
 * end is said in words that match how it ended. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { RemovalWatch } from './watch-removal.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const shown = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: shown } }));

const mocked = vi.mocked(api);

function page(state: string) {
	return { jobs: [{ id: 'j1', state, progress: 0, note: null }], total: 1 };
}

function watcher(): RemovalWatch {
	return new RemovalWatch('face_forget', 'Face data deleted', "Couldn't delete face data");
}

beforeEach(() => {
	vi.clearAllMocks();
	vi.useFakeTimers();
});

afterEach(() => vi.useRealTimers());

describe('a deletion followed from its pane', () => {
	it('holds the press while it runs and says it is done when it is', async () => {
		mocked.get.mockResolvedValue(page('running') as never);
		const watch = watcher();
		watch.follow('j1');
		await vi.advanceTimersByTimeAsync(2000);
		expect(watch.running).toBe(true);

		mocked.get.mockResolvedValue(page('done') as never);
		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.running).toBe(false);
		expect(watch.outcome).toBe('Face data deleted');
		expect(shown).toHaveBeenCalledWith('Face data deleted', { tone: 'success' });
	});

	it('says it could not when the job failed', async () => {
		mocked.get.mockResolvedValue(page('failed') as never);
		const watch = watcher();
		watch.follow('j1');
		await vi.advanceTimersByTimeAsync(2000);

		expect(watch.outcome).toBe("Couldn't delete face data");
		expect(shown).toHaveBeenCalledWith("Couldn't delete face data", { tone: 'error' });
	});
});
