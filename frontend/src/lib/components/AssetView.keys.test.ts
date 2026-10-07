/*
 * Ctrl and an arrow is the file either side on every kind of file the view shows, and a press
 * steps exactly once: on a clip its player answers, on a picture or a GIF this view does.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssetView from './AssetView.svelte';

const served = vi.hoisted(() => ({ detail: {} as Record<string, unknown> }));

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, canSave: false } }));

vi.mock('$app/state', () => ({
	page: { route: { id: '/browse' }, url: new URL('http://localhost/browse'), state: {} }
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: () => {} } }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/collections' || path === '/photo-sets' || path === '/songs')
				return { items: [] };
			if (path === '/records/fields') return { subjects: {} };
			if (/^\/assets\/[^/]+$/.test(path)) return structuredClone(served.detail);
			return [];
		}),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {}
}));

// The clip's plan and source, stood in for: what is under test is who answers the key.
vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: async (id: string) => ({
			route: 'direct',
			reason: 'plays as it is',
			url: `/api/assets/${id}/stream`,
			scale_height: null,
			projected_realtime: null,
			streamable: true,
			duration_ms: 11_000,
			resume_ms: null,
			view_at_ms: 2_750
		}),
		attach: (video: HTMLVideoElement, plan: { url: string }) => {
			video.setAttribute('src', plan.url);
			return { detach: () => video.removeAttribute('src') };
		},
		startAt: () => null
	};
});

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

function file(media_type: string, over: Record<string, unknown> = {}) {
	return {
		id: 'f-1',
		media_type,
		filename: 'a-file',
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
		duration_ms: media_type === 'image' ? null : 11_000,
		width: 1280,
		height: 720,
		enriched: [],
		enriched_by: [],
		links: [],
		playback_repair: null,
		sprite: null,
		art: null,
		...over
	};
}

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
});

async function show(detail: Record<string, unknown>, drawn: string) {
	served.detail = detail;
	const onnext = vi.fn();
	const onprevious = vi.fn();
	instance = mount(AssetView, { target: host, props: { id: 'f-1', onnext, onprevious } });
	await vi.waitFor(() => {
		flushSync();
		if (!host.querySelector(drawn)) throw new Error('nothing drawn yet');
	});
	return { onnext, onprevious };
}

function press(key: string, held: KeyboardEventInit = {}) {
	window.dispatchEvent(new KeyboardEvent('keydown', { key, cancelable: true, ...held }));
	flushSync();
}

describe('Ctrl and an arrow on every kind of file', () => {
	it.each([
		['a picture', file('image'), '.stage img'],
		['a GIF', file('gif'), '.stage img'],
		['a video', file('video'), '.stage video'],
		['a hidden video', file('video', { concealed: true }), '.concealed']
	])('steps once either way on %s', async (_kind, detail, drawn) => {
		const { onnext, onprevious } = await show(detail, drawn);

		press('ArrowRight', { ctrlKey: true });
		expect(onnext).toHaveBeenCalledTimes(1);
		press('ArrowLeft', { ctrlKey: true });
		expect(onprevious).toHaveBeenCalledTimes(1);
	});

	it('leaves a bare arrow on a video to the player, which seeks with it', async () => {
		const { onnext } = await show(file('video'), '.stage video');

		press('ArrowRight');
		expect(onnext).not.toHaveBeenCalled();
		press('ArrowRight', { shiftKey: true });
		expect(onnext).toHaveBeenCalledTimes(1);
	});
});
