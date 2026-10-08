/*
 * Ctrl with an arrow over a clip steps once: the player takes it where it was handed Previous and
 * Next, and the viewer takes it on a phone held by a finger, where the player was handed neither.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssetView from './AssetView.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { finger } from '$lib/components/player/finger.svelte';

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, canSave: false } }));

vi.mock('$app/state', () => ({
	page: { route: { id: '/browse' }, url: new URL('http://localhost/browse'), state: {} }
}));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/records/fields') return { subjects: {} };
			if (path === '/collections' || path === '/photo-sets' || path === '/songs') {
				return { items: [] };
			}
			if (path === '/assets/a-1') return clip;
			return [];
		}),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {}
}));

const clip = vi.hoisted(() => ({
	id: 'a-1',
	media_type: 'video',
	filename: 'a-clip.mp4',
	original_filename: null,
	concealed: false,
	favorite: false,
	rating: null,
	hidden: false,
	hidden_here: false,
	shared: false,
	restricted: false,
	shared_here: false,
	restricted_here: false,
	added_at: 1_700_000_000,
	duration_ms: 60_000,
	width: 1920,
	height: 1080,
	enriched: [],
	enriched_by: [],
	links: [],
	playback_repair: null,
	sprite: null,
	art: null
}));

let instance: ReturnType<typeof mount> | null = null;
let host: HTMLElement;

beforeEach(() => {
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
	phoneWidth.yes = false;
	finger.yes = false;
});

async function pressCtrlRight(onphone: boolean): Promise<number> {
	phoneWidth.yes = onphone;
	finger.yes = onphone;
	const onnext = vi.fn();
	instance = mount(AssetView, {
		target: host,
		props: { id: 'a-1', onnext, onprevious: () => {} }
	});
	await vi.waitFor(() => {
		flushSync();
		if (!host.querySelector('.stage')) throw new Error('nothing drawn yet');
	});
	window.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'ArrowRight', ctrlKey: true, bubbles: true })
	);
	flushSync();
	return onnext.mock.calls.length;
}

describe('Ctrl with an arrow over a clip', () => {
	it.each([
		['at a desk', false],
		['on a phone held by a finger', true]
	])('steps to the next file once %s', async (_where, onphone) => {
		expect(await pressCtrlRight(onphone)).toBe(1);
	});
});
