import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	inFlight: 0,
	post: vi.fn(),
	goto: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: mocks.goto }));
vi.mock('$lib/api/client', () => ({
	api: { post: mocks.post },
	ApiError: class extends Error {},
	requestsInFlight: () => mocks.inFlight > 0,
	setCsrfToken: vi.fn()
}));
vi.mock('$lib/shell/account-scoped', () => ({ stopAccountScopedReaders: vi.fn() }));
vi.mock('$lib/theme/theme.svelte', () => ({ theme: { forget: vi.fn() } }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

import { LANDING_WAIT_MS, signOut } from './sign-out';

/* A read sent while the session is open and answered after it ends is refused, and the browser
   writes the refusal into its console as an error on the sign-in page. */
describe('signing out', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		mocks.inFlight = 0;
		mocks.post.mockReset().mockResolvedValue(undefined);
	});
	afterEach(() => vi.useRealTimers());

	it('ends the session only once the requests already on their way have landed', async () => {
		mocks.inFlight = 1;
		const done = signOut();
		await vi.advanceTimersByTimeAsync(300);
		expect(mocks.post, 'the session ended under a request still on its way').not.toHaveBeenCalled();
		mocks.inFlight = 0;
		await vi.advanceTimersByTimeAsync(100);
		await done;
		expect(mocks.post).toHaveBeenCalledWith('/auth/logout');
	});

	it('signs out anyway when a request never lands', async () => {
		mocks.inFlight = 1;
		const done = signOut();
		await vi.advanceTimersByTimeAsync(LANDING_WAIT_MS + 100);
		await done;
		expect(mocks.post).toHaveBeenCalledWith('/auth/logout');
	});

	it('signs out at once when nothing is on its way', async () => {
		await signOut();
		expect(mocks.post).toHaveBeenCalledWith('/auth/logout');
	});
});
