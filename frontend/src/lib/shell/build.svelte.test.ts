import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BuildWatch } from './build.svelte';

/*
 * A window that has been open across an upgrade.
 *
 * Sift rebuilt and installed on the machine holding the library: a browser picks it up on the next
 * reload, and a desktop client on another computer must be told too, rather than going on drawing
 * the old application until it is quit and started again.
 *
 * These are about the one decision that fixes it (is what the server would serve now the same
 * thing this page is running?) and above all about the two ways of getting it wrong: calling the
 * FIRST answer a change, and calling a server that has nothing built a change.
 */

const fetchMock = vi.fn();

function answer(body: unknown) {
	return {
		ok: true,
		status: 200,
		headers: { get: () => 'application/json' },
		json: async () => body,
		text: async () => JSON.stringify(body)
	};
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	vi.stubGlobal('window', { location: { origin: 'http://sift.test' } });
	fetchMock.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe('whether this window is running the client the server would serve', () => {
	it('takes the first answer as the baseline and says nothing', async () => {
		// Whatever the server says the moment the page starts IS what the page is running. A window
		// that announced an update on its first connection would announce one on every launch.
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'aaaa' }));
		const build = new BuildWatch();

		await build.check();

		expect(build.loaded).toBe('aaaa');
		expect(build.stale).toBe(false);
	});

	it('says so once the answer changes', async () => {
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'aaaa' }));
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'bbbb' }));
		const build = new BuildWatch();

		await build.check();
		await build.check();

		expect(build.stale).toBe(true);
	});

	it('is about the BUILD and not the version, which can be the same across a rebuild', async () => {
		// The same release, rebuilt and reinstalled: a check on the version number would see 0.1.7
		// twice and say nothing.
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'aaaa' }));
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'cccc' }));
		const build = new BuildWatch();

		await build.check();
		await build.check();

		expect(build.stale).toBe(true);
	});

	it('stays quiet when the answer is unchanged, however often it is asked', async () => {
		fetchMock.mockResolvedValue(answer({ version: '0.1.7', build: 'aaaa' }));
		const build = new BuildWatch();

		for (let i = 0; i < 5; i++) await build.check();

		expect(build.stale).toBe(false);
	});

	it('ignores a server with no client built into it', async () => {
		// A source checkout being developed against. There is nothing to compare, and treating the
		// empty string as a build would tell every developer to reload on their first connection.
		fetchMock.mockResolvedValueOnce(answer({ version: '', build: '' }));
		fetchMock.mockResolvedValueOnce(answer({ version: '', build: '' }));
		const build = new BuildWatch();

		await build.check();
		await build.check();

		expect(build.loaded).toBeNull();
		expect(build.stale).toBe(false);
	});

	it('treats an older server that sends no build at all as nothing to say', async () => {
		// The shell and the library are allowed to be different versions: a client-mode window is
		// a different install from the server it points at. A missing field is not a change.
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.6' }));
		const build = new BuildWatch();

		await build.check();

		expect(build.loaded).toBeNull();
		expect(build.stale).toBe(false);
	});

	it('says nothing when the question could not be asked', async () => {
		// A blip, or a session that has not started yet. A banner for a dropped request would be a
		// banner nobody could act on.
		fetchMock.mockResolvedValueOnce(answer({ version: '0.1.7', build: 'aaaa' }));
		fetchMock.mockRejectedValueOnce(new Error('offline'));
		const build = new BuildWatch();

		await build.check();
		await build.check();

		expect(build.stale).toBe(false);
		expect(build.loaded).toBe('aaaa');
	});
});
