/*
 * Leaving the popout for another page, with the clip still going.
 *
 * Pressing a person's chip inside the popout goes to that person's page, and the popout, a panel
 * over what was behind it, goes too. For a press that means "take me there" the clip carries on in
 * the corner instead, as a setting.
 *
 * The three ways it can be wrong are tested: it must not fire when the account has turned it off,
 * nor when the panel is being dismissed rather than left (which a navigation's own type cannot tell
 * apart; see `takeDismissal`), and the corner panel is filled only after the navigation lands, or
 * two players would run one file during the move.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { beforeNavigate } from '$app/navigation';
import { page } from '$app/state';

const planFor = vi.fn(async (_id: string) => ({
	route: 'direct',
	reason: 'plays as it is',
	url: '/api/assets/asset-1/stream',
	scale_height: null,
	projected_realtime: null,
	streamable: true,
	duration_ms: 30_000 as number | null,
	resume_ms: null
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: (id: string) => planFor(id),
		attach: () => ({ detach: () => {} }),
		startAt: () => null
	};
});

vi.mock('$lib/api/client', () => ({
	api: { post: vi.fn(async () => ({})), get: vi.fn(async () => ({})), put: vi.fn(async () => ({})) }
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

/** The account's answer, so a test can set it without going near the server. */
let keepsPlaying = true;

vi.mock('$lib/shell/interface-state.svelte', () => ({
	popoutLeavesToMini: () => keepsPlaying,
	recallInterfaceState: vi.fn(async () => {})
}));

import PlayerHarness from './PlayerHarness.svelte';
import { mini } from '$lib/player/mini.svelte';
import { dismissingAsset, takeDismissal } from '$lib/player/asset-view';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

/** The callback the player registered, and the only way in to what it decides. */
function departing(): (navigation: { complete: Promise<void> }) => void {
	const calls = vi.mocked(beforeNavigate).mock.calls;
	const last = calls[calls.length - 1];
	expect(last, 'the player registered no navigation callback at all').toBeDefined();
	return last[0] as (navigation: { complete: Promise<void> }) => void;
}

/** A clip that is running, at four seconds in. */
function playing(): HTMLVideoElement {
	const video = host.querySelector('video') as HTMLVideoElement;
	Object.defineProperty(video, 'paused', { value: false, configurable: true });
	video.currentTime = 4;
	return video;
}

async function render(compact = false) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerHarness, { target: host, props: { id: 'asset-1', compact } });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

beforeEach(() => {
	keepsPlaying = true;
	mini.close();
	// The panel's own state is a module singleton, so a dismissal left standing by one test would
	// be read by the next, which is exactly the fault the read-once rule exists to stop.
	takeDismissal();
	page.state.asset = 'asset-1';
	vi.mocked(beforeNavigate).mockClear();
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	mini.close();
	delete page.state.asset;
});

describe('leaving the popout while something is playing', () => {
	it('hands the clip to the mini player, at the moment it had got to', async () => {
		await render();
		playing();

		departing()({ complete: Promise.resolve() });
		// Nothing yet: the popout is still on screen, and filling the panel now would be two
		// players on one file for as long as the navigation takes.
		expect(mini.asset).toBeNull();

		await Promise.resolve();
		await Promise.resolve();

		expect(mini.asset?.id).toBe('asset-1');
		expect(mini.asset?.at).toBe(4);
		expect(mini.asset?.paused).toBe(false);
		// NOT a handover: the view is already leaving, and saying otherwise would send the browser
		// back out of the page somebody just asked for.
		expect(mini.handover).toBe(false);
	});

	it('leaves it alone when the account has turned it off', async () => {
		keepsPlaying = false;
		await render();
		playing();

		departing()({ complete: Promise.resolve() });
		await Promise.resolve();
		await Promise.resolve();

		expect(mini.asset).toBeNull();
	});

	it('leaves it alone when the panel is being dismissed rather than left', async () => {
		await render();
		playing();
		// What Escape and the veil say on their way out. The navigation that follows is a `popstate`
		// and a chip's is a `link`, but a panel opened cold closes with a `goto`, which is the same
		// shape as the departure, so the act says which it is rather than being guessed at.
		dismissingAsset();

		departing()({ complete: Promise.resolve() });
		await Promise.resolve();
		await Promise.resolve();

		expect(mini.asset).toBeNull();
	});

	it('leaves it alone when the clip is paused', async () => {
		await render();
		const video = host.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'paused', { value: true, configurable: true });

		departing()({ complete: Promise.resolve() });
		await Promise.resolve();
		await Promise.resolve();

		expect(mini.asset).toBeNull();
	});

	it('leaves it alone when the navigation is cancelled', async () => {
		await render();
		playing();

		departing()({ complete: Promise.reject(new Error('cancelled')) });
		await Promise.resolve();
		await Promise.resolve();

		expect(mini.asset).toBeNull();
	});

	it('is not the panel player answering, which would fill itself from itself', async () => {
		await render(true);

		const calls = vi.mocked(beforeNavigate).mock.calls;
		if (calls.length > 0) {
			(calls[calls.length - 1][0] as (n: { complete: Promise<void> }) => void)({
				complete: Promise.resolve()
			});
			await Promise.resolve();
			await Promise.resolve();
		}

		expect(mini.asset).toBeNull();
	});
});
