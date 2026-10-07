/*
 * How many reads a page costs: one per question, whatever shape its files are, and the pager says
 * only a page that has landed whole.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';

/** The library in order, how many new files sit above it, and page reads held back on request. */
const server = vi.hoisted(() => ({
	reads: [] as Record<string, unknown>[],
	holdAfter: false,
	ids: [] as string[],
	shapes: [] as [number, number][],
	hold: false,
	held: [] as (() => void)[]
}));

const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets') {
				const query = options?.query ?? {};
				server.reads.push(query);
				if ((server.hold && !('from' in query)) || (server.holdAfter && 'after' in query)) {
					await new Promise<void>((release) => server.held.push(release));
				}
				let offset = Number(query.offset ?? 0);
				if ('from' in query) offset = Math.max(0, server.ids.indexOf(String(query.from)));
				if ('after' in query) offset = server.ids.indexOf(String(query.after)) + 1;
				const limit = Number(query.limit ?? 24);
				const items = server.ids.slice(offset, offset + limit).map((id) => {
					const [width, height] = server.shapes[Number(id.replace(/\D/g, '')) % 3] ?? [16, 9];
					return {
						id,
						media_type: 'video',
						duration_ms: 4000,
						width,
						height,
						thumb: true,
						favorite: false,
						rating: 0,
						concealed: false,
						shared: false
					};
				});
				return { items, total: server.ids.length, limit, offset, complete: true };
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		post: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		get url() {
			return at.url;
		}
	}
}));

class FakeObserver {
	static instances: FakeObserver[] = [];
	#callback: IntersectionObserverCallback;
	observed = new Set<Element>();

	constructor(callback: IntersectionObserverCallback) {
		FakeObserver.instances.push(this);
		this.#callback = callback;
	}
	observe(element: Element) {
		this.observed.add(element);
	}
	unobserve(element: Element) {
		this.observed.delete(element);
	}
	disconnect() {
		this.observed.clear();
	}
	fire(target: Element) {
		this.#callback(
			[{ target, isIntersecting: true, intersectionRatio: 1 } as IntersectionObserverEntry],
			this as unknown as IntersectionObserver
		);
	}
}

/* Every resize observer the wall makes, to tell it the box changed. */
const sized = { observers: [] as ResizeObserverCallback[] };
class FakeResize {
	constructor(callback: ResizeObserverCallback) {
		sized.observers.push(callback);
	}
	observe() {}
	unobserve() {}
	disconnect() {}
}

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	FakeObserver.instances = [];
	vi.stubGlobal('IntersectionObserver', FakeObserver);
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => 900
	});
	server.ids = Array.from({ length: 600 }, (_unused, index) => `a${index}`);
	server.shapes = [[16, 9]];
	server.hold = false;
	server.holdAfter = false;
	server.reads = [];
	sized.observers = [];
	vi.stubGlobal('ResizeObserver', FakeResize);
	server.held = [];
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

async function settle() {
	for (let round = 0; round < 6; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

async function wall() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here' }
	});
	await settle();
	await settle();
}

function size(width: number, height: number) {
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => width
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => height
	});
}

const firstPageReads = () =>
	server.reads.filter((query) => Number(query.offset ?? -1) === 0 && !('after' in query));

function readout(): string {
	return host.querySelector('nav.pager .where')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

vi.setConfig({ testTimeout: 30_000 });

describe('the reads a page costs', () => {
	it('sizes a page of portrait clips once: what it brought back does not resize it', async () => {
		size(6000, 900);
		server.shapes = [
			[9, 16],
			[9, 16],
			[9, 16]
		];
		const said: string[] = [];
		const watching = new MutationObserver(() => {
			const now = readout();
			if (/\d-\d/.test(now) && said.at(-1) !== now) said.push(now);
		});
		watching.observe(document.body, { subtree: true, childList: true, characterData: true });
		await wall();
		await settle();
		watching.disconnect();
		expect(firstPageReads(), 'the first page was read again at another size').toHaveLength(1);
		expect(said, 'the page changed size after it landed').toHaveLength(1);
	});

	it('fills a page of narrow files in its first read', async () => {
		server.shapes = [
			[3, 4],
			[3, 4],
			[3, 4]
		];
		await wall();
		await settle();
		expect(server.reads, 'the page needed a second read').toHaveLength(1);
	});

	it('lays the page it holds out again when the box shrinks, reading nothing', async () => {
		await wall();
		const before = server.reads.length;
		size(1200, 600);
		for (const observed of sized.observers) observed([], {} as ResizeObserver);
		await settle();
		await settle();
		expect(server.reads.slice(before), 'a smaller page was read again').toEqual([]);
	});

	it('reads a page that grew past what it holds, from where it stands', async () => {
		size(600, 400);
		await wall();
		const before = server.reads.length;
		size(2400, 1800);
		for (const observed of sized.observers) observed([], {} as ResizeObserver);
		await settle();
		await settle();
		const grown = server.reads.slice(before);
		expect(grown.length, 'a larger page was not read').toBeGreaterThan(0);
		expect(grown.every((query) => Number(query.offset ?? 0) === 0 || 'after' in query)).toBe(true);
	});

	it('says no range while the page is still being topped up', async () => {
		server.shapes = [
			[1, 4],
			[1, 4],
			[1, 4]
		];
		server.holdAfter = true;
		await wall();
		await settle();
		expect(server.held.length, 'the page needed no top-up, so this proves nothing').toBeGreaterThan(
			0
		);
		const said = readout();
		expect(said, 'the first block was said as the page').not.toMatch(/\d-\d/);
		server.holdAfter = false;
		for (const release of server.held.splice(0)) release();
		await settle();
		await settle();
		expect(readout()).toMatch(/^1-\d+ of 600 files$/);
	});
});
