/* Measuring what this machine can do: the start is a POST that answers immediately, the result a GET
 * the screen polls, often enough to feel alive rather than to catch a moment. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

import { fetchSelfTest, POLL_MS, startSelfTest } from './selftest';

const IDLE = {
	running: false,
	measurement: null,
	recommendations: [],
	finished: false
};

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue(IDLE);
	mocks.post.mockResolvedValue({ ...IDLE, running: true });
});

afterEach(() => {
	vi.restoreAllMocks();
});

it('starts a test with a POST that answers straight away', async () => {
	// Starting and reading the result are two requests on purpose: the test works the machine for
	// about a minute, and a request that waited for it would look like a hung screen.
	const started = await startSelfTest();

	expect(mocks.post).toHaveBeenCalledWith('/performance/self-test', { body: {} });
	expect(started.running).toBe(true);
});

it('reads the result with a GET, which is what the polling asks', async () => {
	await fetchSelfTest();

	expect(mocks.get).toHaveBeenCalledWith('/performance/self-test');
});

it('polls often enough for the screen to feel alive rather than to catch a moment', () => {
	// The test takes tens of seconds. A number small enough to be a real poll would be a request a
	// second for a minute, for a number that changes four times.
	expect(POLL_MS).toBeGreaterThanOrEqual(1000);
	expect(POLL_MS).toBeLessThanOrEqual(5000);
});
