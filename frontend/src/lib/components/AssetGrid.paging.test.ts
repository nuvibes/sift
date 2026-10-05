/*
 * Turning pages on a wall that other people and an import are changing: a page somebody turned to
 * stays turned, and Previous shows the page Next left, the same first file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { arrivals, jobChanges, libraryChanges, mine } from '$lib/library/changes.svelte';

/** The library in order, how many new files sit above it, and page reads held back on request. */
const server = vi.hoisted(() => ({
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
				if (server.hold && !('from' in query)) {
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

function firstTile(): string | null {
	return host.querySelector('[data-tile-id]')?.getAttribute('data-tile-id') ?? null;
}

async function press(label: string) {
	const button = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	expect(button, `no ${label} button`).not.toBeNull();
	button?.click();
	await settle();
	await settle();
}

/** The newest file on the page is on screen, so arrivals may come in at the top. */
function topOnScreen() {
	const tile = host.querySelector(`[data-tile-id="${firstTile()}"]`);
	for (const observer of FakeObserver.instances) {
		if (tile && observer.observed.has(tile)) observer.fire(tile);
	}
}

/* A whole wall is mounted and turned: seconds on a loaded runner. */
vi.setConfig({ testTimeout: 30_000 });

describe('a turned page while the library changes', () => {
	it('stays turned when a file arrives just before Next and its wait ends mid-turn', async () => {
		vi.useFakeTimers();
		await wall();
		topOnScreen();
		expect(firstTile()).toBe('a0');

		server.ids = ['new0', ...server.ids];
		arrivals.changed();
		await settle();
		await settle();
		expect(firstTile(), 'one file moved the whole wall').toBe('a0');

		server.hold = true;
		await press('Next page');
		await vi.advanceTimersByTimeAsync(2000);
		server.hold = false;
		for (const release of server.held.splice(0)) release();
		await settle();
		await settle();

		expect(firstTile(), 'Next was thrown back to the first page').not.toBe('a0');
		expect(firstTile()).not.toBe('new0');
	});

	it('is not re-read or moved by each kind of announcement', async () => {
		await wall();
		await press('Next page');
		const turned = firstTile();
		expect(turned).not.toBe('a0');

		for (const bell of [jobChanges, mine, arrivals, libraryChanges]) {
			bell.changed();
			await settle();
			await settle();
			expect(firstTile(), 'an announcement moved the page').toBe(turned);
		}
	});
});

describe('Previous after Next', () => {
	it('shows the page that was left, the same first file, on a wall of mixed shapes', async () => {
		server.shapes = [
			[16, 9],
			[9, 16],
			[4, 3]
		];
		await wall();
		const one = firstTile();
		await press('Next page');
		const two = firstTile();
		await press('Next page');
		expect(firstTile()).not.toBe(two);

		await press('Previous page');
		expect(firstTile(), 'Previous did not return to the page Next left').toBe(two);
		await press('Previous page');
		expect(firstTile()).toBe(one);
	});
});
