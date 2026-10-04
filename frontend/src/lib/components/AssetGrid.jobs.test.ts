/*
 * A picture built while the wall is open reaches the wall without a reload.
 *
 * Pressing Generate on a file with no hover clip builds one in a second or two, and none of it moves
 * the file's row, so the wall's other bells do not ring for it and the tile would go on drawing a
 * file with nothing to play until the page was reloaded. The queue's own message is what says the work
 * finished; the wall re-reads its page on it, and a tile already in view is offered the clip its
 * row now says exists.
 *
 * The known positive comes first: every "it asked for nothing" below is also what a wall that never
 * listens at all would do.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { jobChanges } from '$lib/library/changes.svelte';

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
	it('re-reads the page when the queue moves', async () => {
		await wall();

		jobChanges.changed();
		await settle();

		expect(server.reads, 'the wall did not re-read when the queue moved').toBe(1);
	});

	it('asks for the clip of a tile in view once its row says one was built', async () => {
		await wall();
		inView('a');
		await settle();
		expect(clips, 'a tile with no clip asked for one').toEqual([]);

		// Generate finished: the clip is on disk and the row now says so.
		server.preview = true;
		jobChanges.changed();
		await settle();
		await settle();

		expect(clips.some((source) => source.includes('/api/assets/a/preview'))).toBe(true);
	});

	it('reads once at once and once more at the end of a busy second, not once a beat', async () => {
		// A pass building previews moves the queue on every beat. The first read is at once, so a
		// single Generate lands without a wait; the rest of the second is one more read, not five.
		vi.useFakeTimers();
		await wall();

		for (let beat = 0; beat < 5; beat += 1) {
			jobChanges.changed();
			await settle();
		}
		expect(server.reads, 'the first beat was not read at once').toBe(1);

		await vi.advanceTimersByTimeAsync(1000);
		await settle();

		expect(server.reads, 'the beats inside the second were not one read at its end').toBe(2);
	});
});

describe('a clip refused at its address', () => {
	/*
	 * A row can say a clip exists while its file answers 404 (a cache copied without it, a file
	 * removed by hand). Every beat of a running task re-reads the page and offers the tiles in view
	 * their clips again, and a wall that forgot its refusals on each beat would ask the same
	 * address once a second for as long as the task ran.
	 */
	it('is not asked again while the queue beats, and is asked once its token moves', async () => {
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
			jobChanges.changed();
			await settle();
			await vi.advanceTimersByTimeAsync(1000);
			await settle();
		}
		expect(server.reads, 'the queue beats never re-read the page').toBeGreaterThan(1);
		expect(asked(), 'a refused address was asked again on a beat').toHaveLength(1);

		// Rebuilt: the row's picture token moves, so the clip has a new address to ask.
		server.refusedArt = 'second';
		jobChanges.changed();
		await settle();
		await settle();
		expect(asked()).toEqual(['/api/assets/b/preview?v=first', '/api/assets/b/preview?v=second']);
	});
});
