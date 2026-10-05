/*
 * A picture built while the wall is open reaches it on the arrivals bell, and the queue moving
 * alone reads nothing: a wall that re-read on every beat of the queue threw a turned page back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { arrivals, jobChanges } from '$lib/library/changes.svelte';

/** What the server answers with, and every page read it was asked for. */
const server = vi.hoisted(() => ({
	preview: false,
	reads: 0,
	/* Tile b's picture token while its clip is refused at every address, or null for no clip. */
	refusedArt: null as string | null
}));

const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/assets') {
				server.reads += 1;
				return {
					items: ['a', 'b', 'c'].map((id) => ({
						id,
						media_type: 'video',
						duration_ms: 4000,
						width: 1920,
						height: 1080,
						thumb: true,
						preview: (id === 'a' && server.preview) || (id === 'b' && server.refusedArt !== null),
						art: id === 'b' && server.refusedArt !== null ? server.refusedArt : undefined,
						favorite: false,
						rating: 0,
						concealed: false,
						shared: false
					})),
					total: 3,
					limit: 24,
					offset: 0,
					complete: true
				};
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

/** The browser's own observer, driven by hand. The shape `AssetGrid.arrivals.test.ts` uses. */
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
	fire(target: Element, isIntersecting: boolean) {
		this.#callback(
			[
				{
					target,
					isIntersecting,
					intersectionRatio: isIntersecting ? 1 : 0
				} as IntersectionObserverEntry
			],
			this as unknown as IntersectionObserver
		);
	}
}

/** Every clip the wall asked the network for. */
const clips: string[] = [];

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	FakeObserver.instances = [];
	clips.length = 0;
	server.preview = false;
	server.reads = 0;
	server.refusedArt = null;
	vi.stubGlobal('IntersectionObserver', FakeObserver);
	vi.stubGlobal(
		'fetch',
		vi.fn(async (source: string) => {
			clips.push(String(source));
			if (String(source).includes('/api/assets/b/preview'))
				return new Response(null, { status: 404 });
			return new Response(new Blob(['clip']), { status: 200 });
		})
	);
	// jsdom reports every element as zero wide, and a wall of no width lays out no rows at all.
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
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

async function settle() {
	for (let round = 0; round < 4; round += 1) {
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
	server.reads = 0;
}

/** Bring a tile into view, the way scrolling would. */
function inView(id: string) {
	const tile = host.querySelector(`[data-tile-id="${id}"]`);
	expect(tile, 'the wall drew no tiles, so nothing below could mean anything').not.toBeNull();
	for (const observer of FakeObserver.instances) {
		if (tile && observer.observed.has(tile)) observer.fire(tile, true);
	}
}

describe('a task finishing while the wall is open', () => {
	it('re-reads the page when a picture arrives, and not when only the queue moves', async () => {
		await wall();

		arrivals.changed();
		await settle();
		expect(server.reads, 'the known positive: an arrival did not re-read').toBe(1);

		for (let beat = 0; beat < 5; beat += 1) {
			jobChanges.changed();
			await settle();
		}
		expect(server.reads, 'the queue moving re-read the wall').toBe(1);
	});

	it('asks for the clip of a tile in view once its row says one was built', async () => {
		await wall();
		inView('a');
		await settle();
		expect(clips, 'a tile with no clip asked for one').toEqual([]);

		// Generate finished: the clip is on disk and the row now says so.
		server.preview = true;
		arrivals.changed();
		await settle();
		await settle();

		expect(clips.some((source) => source.includes('/api/assets/a/preview'))).toBe(true);
	});
});

describe('a clip refused at its address', () => {
	/*
	 * A row can say a clip exists while its file answers 404. Every arrival re-reads the page and
	 * offers the tiles in view their clips again, so a forgotten refusal is asked again each time.
	 */
	it('is not asked again on each re-read, and is asked once its token moves', async () => {
		vi.useFakeTimers();
		server.refusedArt = 'first';
		await wall();
		inView('b');
		await settle();
		const asked = () => clips.filter((source) => source.includes('/api/assets/b/preview'));
		expect(asked(), 'the known positive: the tile in view never asked for its clip').toEqual([
			'/api/assets/b/preview?v=first'
		]);

		for (let beat = 0; beat < 4; beat += 1) {
			arrivals.changed();
			await settle();
			await vi.advanceTimersByTimeAsync(1000);
			await settle();
		}
		expect(server.reads, 'the arrivals never re-read the page').toBeGreaterThan(1);
		expect(asked(), 'a refused address was asked again on a beat').toHaveLength(1);

		// Rebuilt: the row's picture token moves, so the clip has a new address to ask.
		server.refusedArt = 'second';
		arrivals.changed();
		await settle();
		await settle();
		expect(asked()).toEqual(['/api/assets/b/preview?v=first', '/api/assets/b/preview?v=second']);
	});
});
