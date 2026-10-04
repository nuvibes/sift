/*
 * A video whose bytes Sift cannot reach says what a picture in the same state says.
 *
 * The playback plan answers 404 when no copy of the file can be read. The row is still in the
 * library, so the player draws the same page the picture viewer does, never a bare "Not found."
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const refused = vi.hoisted(() => {
	class ApiError extends Error {
		constructor(
			readonly status: number,
			readonly detail: string | null
		) {
			super(detail ?? 'refused');
		}
	}
	return { ApiError };
});

vi.mock('$lib/api/client', () => ({
	ApiError: refused.ApiError,
	api: {
		post: vi.fn(async () => ({})),
		get: vi.fn(async () => ({})),
		put: vi.fn(async () => ({}))
	}
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: vi.fn(async () => {
			throw new refused.ApiError(404, 'Not found.');
		}),
		attach: vi.fn(() => ({ detach: vi.fn() })),
		startAt: () => null
	};
});

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

const PlayerHarness = (await import('./PlayerHarness.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

describe('a video Sift cannot reach', () => {
	it('draws the page a picture in the same state draws', async () => {
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(PlayerHarness, {
			target: host,
			props: { id: 'asset-1', sprite: null, art: null }
		});
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		flushSync();
		const said = (host.textContent ?? '').replace(/\s+/g, ' ');
		expect(said).toContain("Sift can't reach this file");
		expect(said).toContain("It's still in the library");
		expect(said).not.toContain('Not found.');
	});
});
