// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Loops wall keeps the page it was left on.
 *
 * It is the one wall of the six drawn by the MEDIA GRID rather than by a wall of cards. The route
 * resolves a mark into its place in this same list, and `LOOP_SOURCE.anchored` is what tells the
 * grid so.
 *
 * ## Why the mock moves the browser's address
 *
 * `replaceState` puts the new address in the bar and never assigns `page.url`, so the screen goes
 * on being handed the address it arrived at. A mock that only records the call cannot show what
 * that costs. The stand-in below does both halves, the way the router does.
 *
 * The first test is the known positive: it asserts the wall asked for the row and drew it. Without
 * it, "the address names the row" is also true of a wall that fetched nothing at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Wall from './+page.svelte';

/** Where the row the address names sits in the scoped, ordered list, as the server has it. */
const AT = 120;

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/loops') }));
const router = vi.hoisted(() => ({ replaced: [] as string[] }));

function mark(offset: number, index: number) {
	return {
		id: `mark${offset + index}`,
		asset_id: `file${offset + index}`,
		name: `Stretch ${offset + index}`,
		start_ms: 0,
		end_ms: 4000,
		created_at: 0,
		duration_ms: 40_000,
		media_type: 'video',
		width: 1920,
		height: 1080,
		original_filename: `clip${offset + index}.mp4`,
		thumb: true,
		still: false,
		art: null,
		preview: null,
		favorite: false,
		pinned: false,
		rating: 0,
		o_count: 0,
		views: 0,
		resume_ms: null,
		concealed: false,
		hidden: false,
		hidden_here: false,
		shared: false,
		shared_here: false,
		restricted: false,
		restricted_here: false,
		unreachable: false,
		whole: false,
		tags: []
	};
}

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/loops') return { items: [], total: 0, limit: 50, offset: 0, complete: true };
			server.asks.push(query);
			// The server resolves a mark into its position in the scoped, ordered list. Here the row
			// the address names sits at `AT`, so the stand-in can answer the same question.
			const anchored = 'from' in query;
			const offset = anchored ? AT : Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			return {
				items: Array.from({ length: limit }, (_x, index) => mark(offset, index)),
				total: 500,
				limit,
				offset,
				complete: true
			};
		}),
		post: vi.fn(async () => undefined),
		put: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn((url: string) => {
		router.replaced.push(url);
		// The half that matters: the bar moves, and `page.url` does not.
		window.history.replaceState({}, '', url);
	})
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	server.asks = [];
	router.replaced = [];
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	window.history.replaceState({}, '', '/');
});

async function settle() {
	for (let round = 0; round < 6; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function wall(address: string) {
	window.history.replaceState({}, '', address);
	at.url = new URL(`${window.location.origin}${address}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Wall, { target: host, props: {} });
	await settle();
	await settle();
	return host;
}

describe('the Loops wall and the mark in its address', () => {
	it('asks the server for the mark the address names, not for an offset', async () => {
		await wall('/loops?from=mark120');
		const first = server.asks[0];
		expect(first.from).toBe('mark120');
		expect(first.offset).toBeUndefined();
	});

	it('leaves the address naming the mark the page actually starts at', async () => {
		await wall('/loops?from=mark120');
		// The server decides where an anchored read lands, so this is the answer's row and not the
		// one that was asked for, which is the whole reason the address is written afterwards.
		expect(window.location.search).toBe(`?from=mark${AT}&near=${AT}`);
	});

	it('never writes that mark onto anything but the wall', async () => {
		await wall('/loops?from=mark120');
		for (const written of router.replaced) {
			expect(written.split('?')[0]).toBe('/loops');
		}
	});

	it('asks for an offset when the address names nothing', async () => {
		await wall('/loops');
		const first = server.asks[0];
		expect(first.from).toBeUndefined();
		expect(Number(first.offset)).toBe(0);
	});
});

describe('the Loops search box keeps its words in the address', () => {
	function box(drawn: HTMLElement): HTMLInputElement | null {
		return drawn.querySelector<HTMLInputElement>('input[placeholder="Search loops"]');
	}

	it('arrives filtered by the words in its address, with them in the box', async () => {
		const drawn = await wall('/loops?called=dusk');
		expect(server.asks[0]?.called).toBe('dusk');
		expect(box(drawn)?.value).toBe('dusk');
	});

	it('writes what is typed into the address, where Back and a link keep it', async () => {
		const { goto } = await import('$app/navigation');
		vi.mocked(goto).mockClear();
		const drawn = await wall('/loops');
		const input = box(drawn);
		expect(input, 'the wall drew no search box').toBeTruthy();
		if (!input) return;
		input.value = 'dusk';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await new Promise((done) => setTimeout(done, 300));

		expect(vi.mocked(goto).mock.calls.at(-1)?.[0]).toBe('/loops?called=dusk');
	});
});
