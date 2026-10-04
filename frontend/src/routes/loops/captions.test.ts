// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Loops wall names its marks and can be searched by those names.
 *
 * A tile on this wall is a picture of the VIDEO, so the name under it is the only thing that tells
 * two marks of one video apart. The name is the mark's own (`name`), never the file's.
 *
 * The layout is arithmetic over the container's width, and jsdom reports every width as zero, so
 * the width and height are stubbed; the rows, the placing and the lines under them are real.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { CAPTION_HEIGHT, GRID_GUTTER } from '$lib/grid/justify';
import Wall from './+page.svelte';

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/loops') }));

const NAMES = ['Beach walk', null, 'Hill top', 'the beach at night', 'Harbour', 'Old pier'];

function mark(index: number) {
	return {
		id: `mark${index}`,
		asset_id: `file${index}`,
		name: NAMES[index % NAMES.length],
		start_ms: 0,
		end_ms: 4000,
		created_at: 0,
		duration_ms: 4000,
		media_type: 'video',
		width: 1920,
		height: 1080,
		original_filename: `clip${index}.mp4`,
		thumb: true,
		still: false,
		art: null,
		preview: false,
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
		whole: true,
		tags: []
	};
}

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/loops') return { items: [], total: 0, limit: 50, offset: 0, complete: true };
			server.asks.push(query);
			const offset = Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			const called = String(query.called ?? '').toLowerCase();
			const all = Array.from({ length: 30 }, (_x, index) => mark(index)).filter(
				(one) => !called || (one.name ?? '').toLowerCase().includes(called)
			);
			return {
				items: all.slice(offset, offset + limit),
				total: all.length,
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
	replaceState: vi.fn((url: string) => window.history.replaceState({}, '', url))
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
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => 900
	});
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

async function wall() {
	window.history.replaceState({}, '', '/loops');
	at.url = new URL(`${window.location.origin}/loops`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Wall, { target: host, props: {} });
	await settle();
	await settle();
	return host;
}

/** Where a placed tile sits, off its `translate3d(x, y, 0)`. */
function yOf(placed: HTMLElement): number {
	const found = /translate3d\(\s*[-\d.]+px,\s*([-\d.]+)px/.exec(placed.style.transform);
	return Number(found?.[1]);
}

describe('the Loops wall', () => {
	it("draws each mark's own name under its tile, and nothing for a mark with no name", async () => {
		const screen = await wall();
		const captions = [...screen.querySelectorAll<HTMLElement>('.placed .caption')];
		expect(captions.length, 'the wall drew no tiles, so nothing was tested').toBeGreaterThan(2);
		expect(captions[0].textContent?.trim()).toBe('Beach walk');
		expect(captions[1].textContent?.trim()).toBe('');
		expect(captions.some((one) => one.textContent?.includes('.mp4'))).toBe(false);
		expect(captions[0].style.height).toBe(`${CAPTION_HEIGHT}px`);
	});

	it('keeps the line under a row inside the row, so the next row starts below it', async () => {
		const screen = await wall();
		const placed = [...screen.querySelectorAll<HTMLElement>('.placed')];
		const rows = [...new Set(placed.map(yOf))].sort((one, other) => one - other);
		expect(rows.length, 'one row cannot show where the next begins').toBeGreaterThan(1);
		const first = placed.find((one) => yOf(one) === rows[0]);
		const height = Number.parseFloat(first?.style.height ?? '0');
		expect(rows[1]).toBe(rows[0] + height + CAPTION_HEIGHT + GRID_GUTTER);
	});

	it('asks for the marks by their own name once typing pauses', async () => {
		const screen = await wall();
		const box = screen.querySelector<HTMLInputElement>('input[type="search"]');
		expect(box?.getAttribute('aria-label')).toBe('Search loops');
		box!.value = 'beach';
		box!.dispatchEvent(new Event('input', { bubbles: true }));
		await new Promise((done) => setTimeout(done, 260));
		await settle();
		expect(server.asks.at(-1)?.called).toBe('beach');
		expect(screen.textContent).not.toContain('Add loop');
	});

	it('asks once for a name that matches nothing, however the empty page measures', async () => {
		/* The pager and the count leave with the rows and come back while a request is out, so the
		   box the rows are measured in changes height with every ask. Each change is a new measure,
		   and a measure that asked again would keep the page asking for as long as it was open. */
		const watchers: ResizeObserverCallback[] = [];
		vi.stubGlobal(
			'ResizeObserver',
			class {
				constructor(callback: ResizeObserverCallback) {
					watchers.push(callback);
				}
				observe() {}
				unobserve() {}
				disconnect() {}
			}
		);
		Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
			configurable: true,
			get: () => (document.querySelector('.pager') ? 560 : 900)
		});
		try {
			const screen = await wall();
			const box = screen.querySelector<HTMLInputElement>('input[type="search"]');
			box!.value = 'zzq';
			box!.dispatchEvent(new Event('input', { bubbles: true }));
			await new Promise((done) => setTimeout(done, 260));
			await settle();
			for (let measure = 0; measure < 6; measure += 1) {
				/* Every watcher on the page hears the change, and a shared one walks the entries it
				   is handed, so each is handed a list as the browser would. */
				for (const watch of watchers) watch([], {} as ResizeObserver);
				await settle();
			}
			expect(server.asks.filter((one) => one.called === 'zzq')).toHaveLength(1);
			expect(screen.textContent).toContain(
				`No loop or the file it's cut from has a name with "zzq" in it.`
			);
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it("starts a rename from the mark's own name, never the name of its file", () => {
		const source = readFileSync('src/routes/loops/+page.svelte', 'utf8');
		expect(source).toContain("rename(item.id, item.name ?? '')");
		expect(source).not.toMatch(/rename\([^)]*original_filename/);
	});
});
