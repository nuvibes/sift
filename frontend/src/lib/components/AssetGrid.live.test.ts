/*
 * A change that lands while nobody is looking at this window.
 *
 * The grid declines to re-read its page behind a hidden window, and must defer the change rather
 * than drop it: the bell is counted as seen when handed over, so there is no second ring. Asserted
 * on what is sent, since the request is the whole of this behaviour and what a test without a
 * browser can see.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { arrivals, assetState, libraryChanges } from '$lib/library/changes.svelte';
import { vault } from '$lib/shell/vault.svelte';
import { LOOP_SOURCE, type RowSource } from '$lib/grid/grid.svelte';
import { mini } from '$lib/player/mini.svelte';

/* A list that cannot turn a row back into its place. Served at `/loops` only because the mock
   answers there; what is under test is the flag, not the address. */
const UNANCHORED: RowSource = { ...LOOP_SOURCE, anchored: false };

const asked = vi.hoisted(() => ({ queries: [] as Record<string, unknown>[] }));
/* A gate the mock waits behind, so a request can be left IN FLIGHT. Without it every ask
   resolves before the next is made and two can never overlap, which is the only state
   the in-flight guard exists for. */
const hold = vi.hoisted(() => ({ open: null as null | (() => void) }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			/* Both row sources, because whether a catch-up anchors is decided by the source's own
			 * `anchored` flag and that is now under test. One row rather than none, since the wall has
			 * to have something at the top of it for there to be an anchor to send. */
			if (path === '/assets' || path === '/loops') {
				asked.queries.push({ path, ...(options?.query ?? {}) });
				if (hold.open) await new Promise<void>((go) => (hold.open = go));
				return {
					items: [
						{
							id: 'one',
							media_type: 'video',
							width: 1920,
							height: 1080,
							duration_ms: 4000,
							thumb: true,
							favorite: false,
							rating: 0,
							concealed: false,
							shared: false
						}
					],
					total: 1,
					limit: 50,
					offset: 0,
					complete: true
				};
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		/* The vault's own writes, because the store under test is the real one: `Vault.#adopt` is
		   what bumps the generation and rings the bell, and a double that did either by hand would
		   be asserting the test's idea of an unlock rather than the application's. */
		post: vi.fn(async (path: string) => {
			if (path === '/vault/unlock') return { unlocked: true, pin_set: true };
			return undefined;
		}),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

// `replaceState` as well as `goto`, because the grid writes the tile it is on into the address
// as you scroll. Without it every scroll rejects with an unhandled error, which vitest warns may
// be making tests pass that should not.
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

let host: HTMLElement;
/* UNMOUNTED, not merely removed. `<svelte:document>` puts its listener on the document, which is
   not inside `host`, so a grid whose element is taken away goes on hearing `visibilitychange` and
   goes on paying what it owes. Left mounted, each test would count the requests of every grid
   before it, which reads exactly like the grid asking twice. */
let mounted: Record<string, unknown> | null = null;
let visible = true;

/** What the window is doing, as the page sees it. jsdom always says visible otherwise. */
function looking(now: boolean): void {
	visible = now;
	document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
	visible = true;
	Object.defineProperty(document, 'visibilityState', {
		configurable: true,
		get: () => (visible ? 'visible' : 'hidden')
	});
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	asked.queries = [];
	hold.open = null;
});

/** Draw the grid the way Browse does, and let its start-up settle. */
async function grid(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here', ...extra }
	});
	await settle();
	asked.queries = [];
}

async function settle() {
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

describe('a change that arrives while this window is behind another one', () => {
	it('re-reads immediately when the window is in front', async () => {
		// The known positive. Without it every assertion below passes on a grid that never re-reads
		// for any reason at all, which is a suite that cannot tell the behaviour from its absence.
		await grid();

		libraryChanges.changed();
		await settle();

		expect(asked.queries.length, 'a visible grid did not re-read at all').toBe(1);
	});

	it('asks for nothing while nobody can see it', async () => {
		await grid();
		looking(false);

		libraryChanges.changed();
		await settle();

		expect(asked.queries.length, 'a hidden window did work for no eyes').toBe(0);
	});

	it('and asks the moment it is looked at again', async () => {
		await grid();
		looking(false);
		libraryChanges.changed();
		await settle();

		looking(true);
		await settle();

		expect(asked.queries.length, 'the change was dropped rather than deferred').toBe(1);
	});

	it('owes one read however many changes arrived while it was away', async () => {
		// A scan rings this on every beat. Coming back to a hundred owed reads would be a hundred
		// requests for one page; what is owed is that the page is stale, which one read settles.
		await grid();
		looking(false);
		for (let ring = 0; ring < 5; ring += 1) libraryChanges.changed();
		arrivals.changed();
		await settle();

		looking(true);
		await settle();

		expect(asked.queries.length).toBe(1);
	});

	it('asks for nothing when nothing happened while it was away', async () => {
		// Coming back must not be a reason to re-read on its own: with the app and a browser both
		// open, every switch between them would be a page fetch in each.
		await grid();
		looking(false);
		await settle();

		looking(true);
		await settle();

		expect(asked.queries.length).toBe(0);
	});
});

describe('how a catch-up asks for the page again', () => {
	it('anchors on the file at the top, so the wall it is showing cannot move', async () => {
		/* The request-level half of keeping the wall still. Re-reading the same OFFSET would show
		 * the arrivals in place, and showing them in place re-lays-out every tile on screen,
		 * so what has to be asserted is the SHAPE of the ask, not just that one was made. */
		await grid();

		libraryChanges.changed();
		await settle();

		expect(asked.queries.length, 'no catch-up was made at all').toBe(1);
		expect(asked.queries[0]).toMatchObject({ path: '/assets', from: 'one' });
		expect(asked.queries[0]).not.toHaveProperty('offset');
	});

	it('does not anchor a wall whose source cannot resolve one', async () => {
		/*
		 * A list that does not take `from` drops the parameter rather than refusing it, so the page
		 * comes back at offset zero with nothing to say why. `RowSource.anchored` records which
		 * sources accept it. No source says false today, so the flag is exercised through a source
		 * declared without it.
		 */
		await grid({ source: UNANCHORED });

		libraryChanges.changed();
		await settle();

		expect(asked.queries.length, 'no catch-up was made at all').toBe(1);
		expect(asked.queries[0]).toMatchObject({ path: '/loops' });
		expect(asked.queries[0], 'an unanchored wall was asked for by anchor').not.toHaveProperty(
			'from'
		);
	});

	it('anchors the wall of marks, whose route resolves a mark into its place', async () => {
		await grid({ source: LOOP_SOURCE });

		libraryChanges.changed();
		await settle();

		expect(asked.queries.length, 'no catch-up was made at all').toBe(1);
		expect(asked.queries[0]).toMatchObject({ path: '/loops', from: 'one' });
		expect(asked.queries[0]).not.toHaveProperty('offset');
	});
});

describe('a catch-up while one is already out', () => {
	it('does not send a second, and sends it when the first lands', async () => {
		/*
		 * The in-flight guard must key on the quiet load too: the catch-up is a quiet load on a
		 * timer, and guarding on `loading` alone would let it re-ask the page every second while
		 * the previous ask was still out.
		 */
		await grid();
		hold.open = () => {};

		libraryChanges.changed();
		await settle();
		expect(asked.queries.length, 'the first catch-up never went out').toBe(1);

		libraryChanges.changed();
		await settle();
		expect(asked.queries.length, 'a second went out while the first was still in flight').toBe(1);

		const go = hold.open;
		hold.open = null;
		go?.();
		await settle();
		await settle();

		expect(asked.queries.length, 'what was owed was dropped rather than deferred').toBe(2);
	});
});

describe('opening and shutting Hidden', () => {
	/*
	 * Unlocking re-reads the page where it is rather than resetting to the top.
	 *
	 * Hidden opening or shutting changes which files this account may see, which is a library
	 * change, and a library change re-reads the page in place. Both directions are covered: a wall
	 * still drawing concealed files after the vault shuts is the worse fault.
	 */
	afterEach(async () => {
		// The store is a module singleton: an unlock left standing is an unlock the next file's
		// tests inherit.
		if (vault.unlocked) await vault.lock();
		asked.queries = [];
	});

	it('unlocking asks once, anchored, and does not go back to the top', async () => {
		await grid();

		expect(await vault.unlock('0000'), 'the unlock itself failed').toBeNull();
		await settle();

		expect(asked.queries.length, 'unlocking fired more than one read of the wall').toBe(1);
		expect(asked.queries[0]).toMatchObject({ path: '/assets', from: 'one' });
		expect(asked.queries[0], 'the wall was re-read from the beginning').not.toHaveProperty(
			'offset'
		);
	});

	it('and shutting it again asks once, anchored, the same way', async () => {
		await grid();
		await vault.unlock('0000');
		await settle();
		asked.queries = [];

		await vault.lock();
		await settle();

		expect(asked.queries.length, 'locking did not re-read the wall exactly once').toBe(1);
		expect(asked.queries[0]).toMatchObject({ path: '/assets', from: 'one' });
		expect(asked.queries[0], 'the wall was re-read from the beginning').not.toHaveProperty(
			'offset'
		);
	});
});

describe('a heart set elsewhere', () => {
	const hearted = {
		asset_id: 'elsewhere',
		favorite: true,
		rating: null,
		views: 0,
		o_count: 0,
		pinned: false
	};

	it('re-reads a wall whose question asks for hearts, so the file can join it', async () => {
		await grid({ query: { fav: 'yes' } });

		assetState.changed(hearted);
		await settle();

		expect(asked.queries.length, 'Favorites never asked again').toBe(1);
	});

	it('asks nothing of a wall a heart cannot move a file into', async () => {
		await grid();

		assetState.changed(hearted);
		await settle();

		expect(asked.queries.length).toBe(0);
	});

	it('asks nothing while the corner panel is open over the wall, and asks once it closes', async () => {
		await grid({ query: { fav: 'yes' } });
		mini.open({ id: 'playing', mediaType: 'video' }, { width: 1400, height: 900 });
		await settle();

		assetState.changed(hearted);
		await settle();
		expect(asked.queries.length, 'the wall reordered under the player').toBe(0);

		mini.close();
		await settle();
		expect(asked.queries.length, 'the held re-read was never made').toBe(1);
	});
});
