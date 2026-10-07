/*
 * The open file follows its own changes.
 *
 * A task finishing on it, or an answer applied to it, rings a bell that names a kind of thing and
 * never the file. The view re-reads its record on either bell and puts it on screen only when the
 * answer moved, so a bell about another file changes nothing here.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssetView from './AssetView.svelte';
import { api } from '$lib/api/client';
import { jobChanges, libraryChanges } from '$lib/library/changes.svelte';

const served = vi.hoisted(() => ({ detail: {} as Record<string, unknown>, gone: false }));

vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true, canSave: false } }));

vi.mock('$app/state', () => ({
	page: { route: { id: '/browse' }, url: new URL('http://localhost/browse'), state: {} }
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: () => {} } }));

vi.mock('$lib/api/client', () => ({
	isMissing: (error: unknown) => (error as { status?: number })?.status === 404,
	api: {
		get: vi.fn(async (path: string) => {
			if (served.gone && /^\/assets\/[^/]+$/.test(path))
				throw Object.assign(new Error('gone'), { status: 404 });
			if (path === '/collections' || path === '/photo-sets' || path === '/songs')
				return { items: [] };
			// The field registry the record grid reads on its first draw. See the record test.
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

/** The shape the detail route answers with, cut down to what this view reads. A photograph, so
 *  the still is drawn rather than the player. */
function detail(over: Record<string, unknown> = {}) {
	return {
		id: 'a-1',
		media_type: 'image',
		filename: 'a-picture.jpg',
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
		duration_ms: null,
		width: 1920,
		height: 1080,
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
	host = document.createElement('div');
	document.body.appendChild(host);
});

afterEach(() => {
	served.gone = false;
	if (instance) unmount(instance);
	instance = null;
	host.remove();
});

/** How many times the record itself was asked for. */
function recordReads(): number {
	return vi.mocked(api.get).mock.calls.filter(([path]) => /^\/assets\/[^/]+$/.test(String(path)))
		.length;
}

async function show(onloaded: (asset: unknown) => void): Promise<void> {
	instance = mount(AssetView, { target: host, props: { id: 'a-1', onloaded } });
	await vi.waitFor(() => {
		flushSync();
		if (!host.querySelector('.acts')) throw new Error('nothing drawn yet');
	});
}

describe('a bell while the file is open', () => {
	it('puts what a finished task wrote on screen without closing the view', async () => {
		served.detail = detail();
		const onloaded = vi.fn();
		await show(onloaded);
		expect(host.textContent).toContain('a-picture.jpg');
		const stage = host.querySelector('.stage');

		served.detail = detail({ filename: 'renamed-by-a-task.jpg' });
		jobChanges.changed();

		await vi.waitFor(() => {
			flushSync();
			if (!host.textContent?.includes('renamed-by-a-task.jpg')) throw new Error('not followed');
		});
		expect(host.querySelector('.stage'), 'the view was rebuilt rather than followed').toBe(stage);
		expect(onloaded).toHaveBeenLastCalledWith(
			expect.objectContaining({ filename: 'renamed-by-a-task.jpg' })
		);
	});

	it('follows the library bell the same way, for an answer applied to the file', async () => {
		served.detail = detail();
		await show(() => {});

		served.detail = detail({ enriched_by: ['stash_box'] });
		const before = recordReads();
		libraryChanges.changed();

		await vi.waitFor(() => {
			flushSync();
			if (recordReads() === before) throw new Error('the record was not asked again');
		});
	});

	it('changes nothing on screen when the answer is the same', async () => {
		served.detail = detail();
		const onloaded = vi.fn();
		await show(onloaded);
		const told = onloaded.mock.calls.length;
		const before = recordReads();

		jobChanges.changed();
		libraryChanges.changed();

		await vi.waitFor(() => {
			flushSync();
			if (recordReads() === before) throw new Error('the record was not asked again');
		});
		// Both bells, one read: they are settled together.
		expect(recordReads() - before).toBe(1);
		expect(onloaded.mock.calls.length, 'the same answer was put on screen again').toBe(told);
	});

	it('asks for the record once the library bell has settled, never on the bell itself', async () => {
		served.detail = detail();
		await show(() => {});
		const before = recordReads();

		libraryChanges.changed();
		flushSync();

		expect(recordReads(), 'a re-read of the file restarts the sitting it reports').toBe(before);
		await vi.waitFor(() => expect(recordReads()).toBe(before + 1));
	});

	it('re-reads the people and filings under the file on the library bell itself', async () => {
		served.detail = detail();
		await show(() => {});
		const bandReads = () =>
			vi.mocked(api.get).mock.calls.filter(([path]) => String(path) === '/assets/a-1/people')
				.length;
		const before = bandReads();

		libraryChanges.changed();
		flushSync();

		expect(bandReads(), 'a tag or a person applied elsewhere shows under the file at once').toBe(
			before + 1
		);
	});

	it('reads the band once for a library bell, and after the settle only for a jobs bell', async () => {
		served.detail = detail();
		await show(() => {});
		const bandReads = () =>
			vi.mocked(api.get).mock.calls.filter(([path]) => String(path) === '/assets/a-1/people')
				.length;
		let before = bandReads();
		let records = recordReads();

		libraryChanges.changed();
		flushSync();
		await vi.waitFor(() => expect(recordReads()).toBe(records + 1));
		expect(bandReads(), 'the band read again after the settle it had already had').toBe(before + 1);

		before = bandReads();
		records = recordReads();
		jobChanges.changed();
		flushSync();
		expect(bandReads()).toBe(before);
		await vi.waitFor(() => expect(recordReads()).toBe(records + 1));
		expect(bandReads(), 'a job that moved the file reads its band once settled').toBe(before + 1);
	});

	it('says the file is gone when it was deleted somewhere else', async () => {
		served.detail = detail();
		await show(() => {});

		served.gone = true;
		libraryChanges.changed();

		await vi.waitFor(() => {
			flushSync();
			if (!host.textContent?.includes('Not found.')) throw new Error('still drawn as here');
		});
	});
});
