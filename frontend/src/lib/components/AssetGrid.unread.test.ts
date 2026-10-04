/*
 * A tile with no picture must not claim work is happening when none is.
 *
 * "Importing..." and the shimmer are a promise, and `Tile.placeholder` exists to avoid making it: a
 * pulsing placeholder that never resolves is a lie. Files are taken into the library first and read
 * afterwards, so a job queue stopped part-way through an import leaves most of them with no
 * dimensions, no picture and nothing coming for them. Drawn as importing, every one of those would
 * shimmer for ever.
 *
 * ## What is asserted
 *
 * The word on the tile against the one fact that decides it: whether picture work is in flight
 * (`imports.busy`), a file's first pictures or ones made again. The header's "Importing N files"
 * counts only files arriving (`imports.arriving`), which is a narrower question.
 *
 * The two quiet answers are kept apart on purpose, because a person can act on the difference: a
 * file with no width has never been read and only a scan of its folder will fix it, while one that
 * has been read is only missing its picture and the catch-up in Importing builds that.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { imports } from '$lib/library/imports.svelte';

const page = vi.hoisted(() => ({ items: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/assets') {
				return {
					items: page.items,
					total: page.items.length,
					limit: page.items.length,
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

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

/** A file with no picture. `width` absent is a file nobody has read; present is one only missing
 *  its still. */
function file(id: string, read: boolean) {
	return {
		id,
		media_type: 'video',
		duration_ms: 4000,
		thumb: false,
		favorite: false,
		rating: 0,
		concealed: false,
		shared: false,
		...(read ? { width: 1920, height: 1080 } : {})
	};
}

beforeEach(() => {
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
	page.items = [];
	imports.busy = 0;
});

async function settle() {
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
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

describe('a tile with no picture', () => {
	it('says it is importing while work really is arriving', async () => {
		// The known positive. Without it every assertion below is satisfied by a grid that never says
		// anything at all, which is exactly what a wall of no width would produce.
		imports.busy = 3;
		page.items = [file('a', false)];

		const where = await wall();

		expect(where.textContent, 'nothing said work was happening').toContain('Importing');
	});

	it('stops saying so the moment nothing is arriving', async () => {
		imports.busy = 0;
		page.items = [file('a', false)];

		const where = await wall();

		expect(where.textContent, 'the tile promised work that is not coming').not.toContain(
			'Importing'
		);
	});

	it('says a file nobody has read has not been read', async () => {
		imports.busy = 0;
		page.items = [file('a', false)];

		await wall();

		expect(host.querySelector('[aria-label="Not read yet"]')).not.toBeNull();
		// DRAWN, not only announced. An empty grey box says no more than a shimmer that lies: the
		// words have to be on the screen, and they have to be the same words the accessible name
		// gives, or the two come to disagree about what the tile is.
		expect(host.textContent, 'the tile said nothing a person could read').toContain('Not read yet');
	});

	it('says a file that HAS been read is only missing its picture', async () => {
		/* The half that keeps the two apart. Both are quiet tiles and they have different answers:
		 * one needs a scan of its folder, the other needs the catch-up, so a single word for both
		 * would send somebody to the wrong button. */
		imports.busy = 0;
		page.items = [file('a', true)];

		await wall();

		expect(host.querySelector('[aria-label="No picture yet"]')).not.toBeNull();
		expect(host.querySelector('[aria-label="Not read yet"]')).toBeNull();
	});
});

describe('a tile whose file Sift cannot reach', () => {
	/*
	 * Every copy missing and no still ever built: the tile that looks most like an ordinary one.
	 * Its gone mark must be drawn, and it must not read "Importing..." for a file nothing can bring
	 * in. Driven through the wall, which passes `importing`, rather than through `Tile` alone.
	 */
	function gone(id: string) {
		return { ...file(id, true), unreachable: true };
	}

	it('wears the gone mark and says so, while other work is arriving', async () => {
		imports.busy = 3;
		page.items = [gone('g')];

		await wall();

		expect(host.querySelector('.mark.gone'), 'the gone mark was not drawn').not.toBeNull();
		expect(host.textContent, 'it promised an import that cannot happen').not.toContain('Importing');
		expect(host.querySelector(`[aria-label="Can't reach this file"]`)).not.toBeNull();
	});

	it('and when nothing is arriving, rather than promising a picture', async () => {
		imports.busy = 0;
		page.items = [gone('g')];

		await wall();

		expect(host.querySelector('.mark.gone'), 'the gone mark was not drawn').not.toBeNull();
		expect(host.querySelector('[aria-label="No picture yet"]')).toBeNull();
		expect(host.textContent).toContain("Can't reach this file");
	});

	it('leaves a reachable file with no picture exactly as it was', async () => {
		// The known negative: the change is about the missing copy, not about every blank tile.
		imports.busy = 3;
		page.items = [file('a', true)];

		await wall();

		expect(host.querySelector('.mark.gone')).toBeNull();
		expect(host.textContent).toContain('Importing');
	});
});
