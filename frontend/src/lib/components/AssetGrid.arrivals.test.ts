/*
 * New files come in by themselves while the top of the wall is on screen.
 *
 * A justified wall re-partitions every row behind an insertion, so a file landing at the head moves
 * every tile behind it, and re-reading on every arrival strobes. The movement is allowed and its
 * rate bounded by two conditions that must both hold, each asserted on its own because either alone
 * is a different feature:
 *
 * - Only while the newest file on the page is on screen. Somebody reading further down is looking
 *   at something, and putting files above it moves what they are reading.
 * - And only for a row's worth, or once arrivals stop. The unit is a row because a row is what the
 *   disturbance costs, and the pause keeps one file dropped into a watched folder from waiting for a
 *   row that never arrives.
 *
 * The known positive matters: every "it did not move" assertion is also satisfied by a grid that
 * draws nothing, which is what a jsdom wall of no width produces. The first test proves the others
 * mean something.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { imports } from '$lib/library/imports.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

/** What the server currently answers with. `held` is what an ANCHORED read gets back (the same
 *  files, sitting further down the library) and `fresh` is what reading a plain offset gets. */
const server = vi.hoisted(() => ({
	held: [] as Record<string, unknown>[],
	fresh: [] as Record<string, unknown>[],
	drift: 0
}));

const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets') {
				const query = options?.query ?? {};
				const anchored = 'from' in query;
				return {
					items: anchored ? server.held : server.fresh,
					total: 500,
					limit: 24,
					offset: anchored ? server.drift : Number(query.offset ?? 0),
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

/** The browser's own observer, driven by hand. Copied in shape from `viewport.svelte.test.ts`. */
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

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function file(id: string) {
	return {
		id,
		media_type: 'video',
		duration_ms: 4000,
		width: 1920,
		height: 1080,
		thumb: true,
		favorite: false,
		rating: 0,
		concealed: false,
		shared: false
	};
}

beforeEach(() => {
	FakeObserver.instances = [];
	vi.stubGlobal('IntersectionObserver', FakeObserver);
	// jsdom reports every element as zero wide, and a wall of no width lays out no rows at all.
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => 900
	});
	server.held = ['a', 'b', 'c', 'd', 'e', 'f'].map(file);
	server.fresh = server.held;
	server.drift = 0;
	imports.busy = 0;
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	imports.busy = 0;
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
	return host;
}

/** The tile drawn for a file, by the attribute the grid stamps every tile with. */
function tileFor(id: string): Element | null {
	return host.querySelector(`[data-tile-id="${id}"]`);
}

/** Say whether the newest file the page holds is on screen, the way scrolling would. */
function topOfWallOnScreen(visible: boolean) {
	const tile = tileFor('a');
	expect(tile, 'the wall drew no tiles, so nothing below could mean anything').not.toBeNull();
	for (const observer of FakeObserver.instances) {
		if (tile && observer.observed.has(tile)) observer.fire(tile, visible);
	}
}

/** Files land above the page: the anchor drifts down the library by `count`, and a plain read of
 *  the page now starts with them. */
function filesArrive(count: number) {
	server.drift = count;
	server.fresh = [
		...Array.from({ length: count }, (_unused, index) => file(`new${index}`)),
		...server.held
	];
}

describe('files arriving while the wall is being watched', () => {
	it('brings them in with nothing pressed, when the top of the wall is on screen', async () => {
		await wall();
		topOfWallOnScreen(true);

		filesArrive(20);
		imports.busy = 1;
		await settle();
		await settle();

		expect(tileFor('new0'), 'the arrivals never came in on their own').not.toBeNull();
		expect(host.textContent, 'it still offered a button for arrivals it had taken').not.toContain(
			'new'
		);
	});

	it('holds the wall still for somebody reading further down', async () => {
		await wall();
		topOfWallOnScreen(false);

		filesArrive(20);
		imports.busy = 1;
		await settle();
		await settle();

		expect(tileFor('new0'), 'the wall moved under somebody who was reading it').toBeNull();
		expect(host.textContent, 'it took the files and said nothing about them').toContain('20');
	});

	it('waits for a row rather than moving the wall for one file', async () => {
		vi.useFakeTimers();
		await wall();
		topOfWallOnScreen(true);

		filesArrive(1);
		imports.busy = 1;
		await settle();
		await settle();

		expect(tileFor('new0'), 'one arrival moved the whole wall').toBeNull();
	});

	it('offers one file in its line immediately when nothing else is arriving, moving nothing', async () => {
		vi.useFakeTimers();
		await wall();
		topOfWallOnScreen(true);

		filesArrive(1);
		imports.busy = 0;
		libraryChanges.changed();
		await settle();
		await settle();

		expect(tileFor('new0'), 'one file moved the wall by itself').toBeNull();
		expect(host.textContent?.replace(/\s+/g, ' '), 'the file was not offered').toContain('1 new');

		await vi.advanceTimersByTimeAsync(2000);
		await settle();
		await settle();
		expect(tileFor('new0'), 'the offered file came in by itself after the hold').toBeNull();
		expect(
			host.textContent?.replace(/\s+/g, ' '),
			'the line it was offered in went away'
		).toContain('1 new');
	});

	it('takes what was waiting when somebody scrolls back to the top', async () => {
		/* The other way arrivals become takeable, and it is not a change to the count, so the
		 * effect that watches the count never runs for it. Without its own hook, a page scrolled
		 * back to the top holds what is waiting until the NEXT file lands, which on a library that
		 * has finished importing is for ever. */
		await wall();
		topOfWallOnScreen(false);

		filesArrive(20);
		imports.busy = 1;
		await settle();
		await settle();
		expect(tileFor('new0'), 'it moved the wall under somebody reading it').toBeNull();

		topOfWallOnScreen(true);
		await settle();
		await settle();

		expect(tileFor('new0'), 'scrolling back to the top left the arrivals stranded').not.toBeNull();
	});

	it('takes what is waiting once nothing more is arriving', async () => {
		vi.useFakeTimers();
		await wall();
		topOfWallOnScreen(true);

		filesArrive(1);
		imports.busy = 1;
		await settle();
		await settle();
		expect(tileFor('new0')).toBeNull();

		await vi.advanceTimersByTimeAsync(2000);
		await settle();
		await settle();

		expect(tileFor('new0'), 'a file dropped into a watched folder never appeared').not.toBeNull();
	});
});
