/*
 * Swap mode on a wall of files: a press on a tile picks its file for the swap instead of opening
 * it, a second press takes it out, and a Hidden tile is not picked at all.
 *
 * The harness is the select-all suite's (a sized box, the paged reader mocked), with twelve files.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { session } from '$lib/shell/session.svelte';
import { swapMode } from '$lib/swap/mode.svelte';

/* A library of this many files, answered a page at a time as the server does. */
const LIBRARY = 12;

const asked = vi.hoisted(() => ({
	reads: [] as Record<string, unknown>[],
	/** Every batch of ids a bulk write was given, in the order the chunks went out. */
	favorited: [] as string[][]
}));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets' || path === '/loops') {
				const query = options?.query ?? {};
				asked.reads.push({ path, ...query });
				const offset = Number(query.offset ?? 0);
				const limit = Number(query.limit ?? 50);
				const count = Math.max(0, Math.min(limit, LIBRARY - offset));
				/* A wall of MOMENTS: the row has an identity of its own and the file it was cut from
				   is a field on it. That is the one wall where a row id is not a file id, and it is
				   why the whole-query read hands back rows rather than ids. */
				const mark = path === '/loops';
				return {
					items: Array.from({ length: count }, (_, each) => ({
						id: mark ? `m${offset + each}` : `a${offset + each}`,
						...(mark ? { asset_id: `a${offset + each}`, start_ms: 0, end_ms: 4000 } : {}),
						media_type: 'video',
						width: 1920,
						height: 1080,
						duration_ms: 4000,
						thumb: true,
						favorite: false,
						rating: 0,
						concealed: false,
						shared: false
					})),
					total: LIBRARY,
					limit,
					offset,
					complete: true
				};
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		post: vi.fn(async (path: string, options?: { body?: Record<string, unknown> }) => {
			if (path === '/assets/favorite') {
				asked.favorited.push((options?.body?.asset_ids ?? []) as string[]);
			}
			return { changed: 0, skipped: 0, reason: null };
		}),
		put: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

/* A BOX WITH A SIZE, because jsdom gives every element none and the wall lays itself out against
   the width it measures, so without this the grid draws a page of zero rows and there is no tile
   to pick. The numbers are an ordinary window; nothing here depends on which. */
function sized(width: number, height: number): () => void {
	const was = {
		w: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth'),
		h: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
	};
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => width
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => height
	});
	return () => {
		if (was.w) Object.defineProperty(HTMLElement.prototype, 'clientWidth', was.w);
		if (was.h) Object.defineProperty(HTMLElement.prototype, 'clientHeight', was.h);
	};
}

let unsize: (() => void) | null = null;

beforeEach(() => {
	asked.reads = [];
	asked.favorited = [];
	unsize = sized(1200, 900);
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	unsize?.();
	unsize = null;
});

async function settle() {
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

/** Draw the grid the way Browse does, and let its start-up settle. */
async function grid(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here', ...extra }
	});
	await settle();
	asked.reads = [];
}

/** Every tile the wall has drawn. */
function tiles(): HTMLElement[] {
	return [...host.querySelectorAll('.placed')] as HTMLElement[];
}

afterEach(() => {
	swapMode.leave();
	session.viewer = undefined;
});

describe('a wall of files in swap mode', () => {
	it('picks the file a tile is a picture of, and a second press takes it out', async () => {
		session.viewer = { role: 'admin' } as typeof session.viewer;
		await grid();
		swapMode.enter();
		flushSync();
		const first = tiles()[0];
		expect(first, 'the wall drew no tiles at all').toBeDefined();

		first.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
		flushSync();
		// The FILE the tile is a picture of, which on this wall is the row itself.
		expect(swapMode.picks.map((one) => [one.kind, one.id])).toEqual([['asset', 'a0']]);
		expect(first.querySelector('.pick[data-purpose="swap"]')).not.toBeNull();
		// Nothing was opened: the press was the pick.
		const { goto } = await import('$app/navigation');
		expect(goto).not.toHaveBeenCalled();

		first.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
		flushSync();
		expect(swapMode.picks).toEqual([]);
		expect(first.querySelector('.pick[data-purpose="swap"]')).toBeNull();
	});

	it('picks every file a drag crosses for the swap, and nothing for the wall', async () => {
		session.viewer = { role: 'admin' } as typeof session.viewer;
		await grid();
		swapMode.enter();
		flushSync();
		const drawnTiles = tiles().slice(0, 3);
		expect(drawnTiles).toHaveLength(3);
		document.elementFromPoint = (x: number) => drawnTiles[Math.floor(x / 100)] ?? null;
		vi.useFakeTimers();
		try {
			drawnTiles[0].dispatchEvent(
				new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 5 })
			);
			vi.advanceTimersByTime(400);
			window.dispatchEvent(new PointerEvent('pointermove', { clientX: 150, clientY: 5 }));
			window.dispatchEvent(new PointerEvent('pointermove', { clientX: 250, clientY: 5 }));
			window.dispatchEvent(new PointerEvent('pointerup'));
			drawnTiles[2].dispatchEvent(
				new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
			);
			flushSync();
		} finally {
			vi.useRealTimers();
		}
		expect(swapMode.picks.map((one) => [one.kind, one.id])).toEqual([
			['asset', 'a0'],
			['asset', 'a1'],
			['asset', 'a2']
		]);
		// The wall's own selection bar never appeared: the drag was the swap's.
		expect(host.querySelector('[aria-selected="true"]')).toBeNull();
	});

	it('draws no swap mark and picks nothing while the mode is off', async () => {
		session.viewer = { role: 'admin' } as typeof session.viewer;
		await grid();
		const first = tiles()[0];
		first.dispatchEvent(
			new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true })
		);
		flushSync();
		expect(swapMode.picks).toEqual([]);
		expect(host.querySelector('.pick[data-purpose="swap"]')).toBeNull();
	});
});
