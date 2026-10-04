/*
 * The popout never flashes while a clip plays.
 *
 * A view is counted a quarter of the way into a clip, and the next bell after it reads the file's
 * record again: the count moved, so the record is a new object. Anything under the picture handed
 * the file as `asset.id` read through that object would take the new record for a new file and
 * empty itself (the faces, the lookalikes, the songs, the organize answer), and the page would
 * close up under the player and fill out again a few seconds into the clip and on every repeat.
 *
 * So the case drives exactly that: a clip playing, its record changing under a bell, the element
 * receiving the events it receives while playing. Then it asks the two things a flash is made of:
 * whether anything under the picture was asked for again (an emptied strip is always followed by
 * its fetch), and whether the stage, the element or its source moved.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssetView from './AssetView.svelte';
import { api } from '$lib/api/client';
import { jobChanges } from '$lib/library/changes.svelte';

const served = vi.hoisted(() => ({ detail: {} as Record<string, unknown> }));
const attached = vi.hoisted(() => ({ attaches: 0, detaches: 0 }));

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

/* The plan and the attaching, stood in for: what is under test is whether the player is ever told
   to let go of what it is playing, and the attach is where it would be. The stand-in sets the
   source the way the real one does, so a second attach would show as a second source. */
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
			attached.attaches += 1;
			video.setAttribute('src', plan.url);
			return {
				detach: () => {
					attached.detaches += 1;
					video.removeAttribute('src');
				}
			};
		},
		startAt: () => null
	};
});

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

/** The record of a clip that has faces in it, cut down to what this view reads. */
function clip(over: Record<string, unknown> = {}) {
	return {
		id: 'v-1',
		media_type: 'video',
		filename: 'a-reel.mp4',
		original_filename: null,
		concealed: false,
		favorite: false,
		rating: null,
		views: 1,
		last_viewed_at: 1_700_000_000,
		hidden: false,
		hidden_here: false,
		shared: false,
		restricted: false,
		shared_here: false,
		restricted_here: false,
		added_at: 1_700_000_000,
		duration_ms: 11_000,
		width: 720,
		height: 1280,
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
	vi.mocked(api.get).mockClear();
	attached.attaches = 0;
	attached.detaches = 0;
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
});

/** How many times anything under the picture asked about the file again. */
function underReads(): Record<string, number> {
	const counted: Record<string, number> = {};
	for (const [path] of vi.mocked(api.get).mock.calls) {
		const part = /^\/assets\/[^/]+\/(faces|similar|same-music|organize)$/.exec(String(path));
		if (part) counted[part[1]] = (counted[part[1]] ?? 0) + 1;
	}
	return counted;
}

function recordReads(): number {
	return vi.mocked(api.get).mock.calls.filter(([path]) => /^\/assets\/[^/]+$/.test(String(path)))
		.length;
}

describe('a clip playing in the popout while its record changes', () => {
	it('keeps the stage, the element and its source, and asks nothing under the picture again', async () => {
		served.detail = clip();
		instance = mount(AssetView, { target: host, props: { id: 'v-1' } });
		await vi.waitFor(() => {
			flushSync();
			if (!host.querySelector('.stage video')?.getAttribute('src')) throw new Error('not playing');
		});
		// Every first read has landed before the count is taken.
		await vi.waitFor(() => {
			if (!underReads().similar || !underReads().faces) throw new Error('not asked yet');
		});
		const stage = host.querySelector('.stage') as HTMLElement;
		const video = stage.querySelector('video') as HTMLVideoElement;
		const source = video.getAttribute('src');
		const children = [...stage.children];
		const before = underReads();
		const moved: MutationRecord[] = [];
		const watcher = new MutationObserver((records) => moved.push(...records));
		watcher.observe(stage, { childList: true });
		watcher.observe(video, { attributes: true, attributeFilter: ['src', 'poster'] });

		// The clip plays past the moment its view is counted; the record then says so.
		for (const name of ['play', 'playing', 'timeupdate', 'timeupdate', 'timeupdate']) {
			video.dispatchEvent(new Event(name));
		}
		served.detail = clip({ views: 2, last_viewed_at: 1_700_000_004 });
		const reads = recordReads();
		jobChanges.changed();
		await vi.waitFor(() => {
			flushSync();
			if (recordReads() === reads) throw new Error('the record was not read again');
		});
		// Past the settle and the read, so anything that was going to ask has asked.
		await new Promise((done) => setTimeout(done, 50));
		flushSync();
		for (const name of ['timeupdate', 'timeupdate', 'pause', 'play', 'timeupdate']) {
			video.dispatchEvent(new Event(name));
		}
		flushSync();
		watcher.disconnect();

		expect(underReads(), 'a strip under the picture emptied and asked again').toEqual(before);
		expect(host.querySelector('.stage'), 'the stage was rebuilt').toBe(stage);
		expect(stage.querySelector('video'), 'the element was replaced').toBe(video);
		expect(video.getAttribute('src')).toBe(source);
		expect([...stage.children]).toEqual(children);
		expect(moved, 'the stage or the element changed while playing').toEqual([]);
		expect(attached).toEqual({ attaches: 1, detaches: 0 });
	});
});
