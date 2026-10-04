/* Ctrl+L: the session shuts on the server, and nothing then asks it for something it refuses.
 *
 * A locked session refuses every vault request, and the server shuts Hidden along with the
 * session, so a launch lock or a trigger still due after the press would only be answered 423. The
 * real vault store is used here, so the press and the later lock meet as they do in the shell.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	post: vi.fn(),
	get: vi.fn(),
	goto: vi.fn(async () => undefined),
	load: vi.fn(async () => undefined),
	forget: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: mocks.goto }));
vi.mock('$lib/api/client', () => ({
	api: { post: mocks.post, get: mocks.get },
	ApiError: class extends Error {}
}));
vi.mock('$lib/shell/session.svelte', () => ({
	session: { load: mocks.load, forget: mocks.forget }
}));

import { lockSift } from './lock-sift';
import { Vault, vault } from './vault.svelte';

beforeEach(() => {
	vi.resetAllMocks();
	mocks.goto.mockResolvedValue(undefined);
});

describe('Ctrl+L', () => {
	it('locks the session and asks nothing of Hidden afterwards, which the server already shut', async () => {
		mocks.post.mockResolvedValueOnce({ outcome: 'locked' });

		await lockSift();
		const reached = await vault.lock();

		expect(mocks.post.mock.calls.map(([path]) => path)).toEqual(['/auth/lock']);
		expect(reached).toBe(true);
		expect(mocks.goto).toHaveBeenCalledWith('/locked', { replaceState: true });
	});

	it('signs out where the server ended the session, and goes to the door', async () => {
		mocks.post.mockResolvedValueOnce({ outcome: 'signed_out' });

		await lockSift();

		expect(mocks.forget).toHaveBeenCalled();
		expect(mocks.goto).toHaveBeenCalledWith('/login', { replaceState: true });
	});

	it('asks again once the session answers, so Hidden still shuts after it reopens', async () => {
		const store = new Vault();
		store.sessionLocked();
		mocks.get.mockResolvedValueOnce({ unlocked: true, pin_set: true });
		mocks.post.mockResolvedValueOnce(undefined);

		await store.load();
		await store.lock();

		expect(mocks.post).toHaveBeenCalledWith('/vault/lock', { keepalive: undefined });
		expect(store.unlocked).toBe(false);
	});
});
