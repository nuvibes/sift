/* A collection longer than one page is walked to its end by the pager, as every wall of files is. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import { screenBar } from '$lib/components/shell/screen-bar.svelte';
import { gridSort } from '$lib/grid/sort-state.svelte';

const LENGTH = 250;
const asked = vi.hoisted(() => ({
	reads: [] as Record<string, unknown>[],
	mixed: false
}));

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const answer = vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
		if (path === '/collections/c1/items') {
			const query = options?.query ?? {};
			asked.reads.push(query);
			const offset = Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 50);
			const count = Math.max(0, Math.min(limit, LENGTH - offset));
			return {
				items: Array.from({ length: count }, (_, each) => ({
					id: `a${offset + each}`,
					media_type: 'video',
					width: asked.mixed && (offset + each) % 3 === 0 ? 1080 : 1920,
					height: asked.mixed && (offset + each) % 3 === 0 ? 1920 : 1080,
					duration_ms: 4000,
					thumb: true,
					art: null,
					original_filename: `clip ${offset + each}.mp4`,
					pinned: false,
					favorite: false,
					rating: null,
					concealed: false
				})),
				total: LENGTH,
				limit,
				offset
			};
		}
		if (path === '/collections/c1') return { id: 'c1', name: 'Long reel', item_count: LENGTH };
		if (path === '/collections/c1/tags') return [];
		return { items: [], total: 0 };
	});
	return { ...real, api: { get: answer, post: answer, put: answer, del: answer } };
});

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn(),
	pushState: vi.fn(),
	afterNavigate: vi.fn(),
	beforeNavigate: vi.fn()
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { state: {}, params: { id: 'c1' }, url: new URL('http://localhost/collections/c1') }
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;
let unsize: (() => void) | undefined;

/* A box with a size, since jsdom gives every element none. */
function sized(width: number, height: number): () => void {
	const was = ['clientWidth', 'clientHeight'].map(
		(name) => [name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)] as const
	);
	for (const [name, value] of [
		['clientWidth', width],
		['clientHeight', height]
	] as const)
		Object.defineProperty(HTMLElement.prototype, name, { configurable: true, get: () => value });
	return () => {
		for (const [name, old] of was) if (old) Object.defineProperty(HTMLElement.prototype, name, old);
	};
}

beforeEach(() => {
	asked.reads = [];
	asked.mixed = false;
	gridSort.set('newest');
	session.viewer = { role: 'admin' } as Viewer;
	unsize = sized(1200, 900);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	unsize?.();
	session.viewer = undefined;
});

async function settle() {
	for (let turn = 0; turn < 10; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

function tiles(): string[] {
	return [...host!.querySelectorAll('[data-tile-id]')].map((one) =>
		one.getAttribute('data-tile-id')!
	);
}

async function open() {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	await settle();
}

/** The words of the menu a right-click on the first tile opens. */
async function menuWords(): Promise<string> {
	host!
		.querySelector('[data-tile-id] [data-context-menu-trigger]')!
		.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }));
	let words = '';
	await vi.waitFor(() => {
		words = document.querySelector('.ui-menu')?.textContent ?? '';
		expect(words).toContain('Use as the cover');
	});
	return words;
}

describe('a collection of 250 files', () => {
	it('draws every one of them by the time the pager reaches the end', async () => {
		await open();

		const seen = new Set(tiles());
		let pages = 1;
		for (; pages < 50; pages += 1) {
			const next = host!.querySelector<HTMLButtonElement>('button[aria-label="Next page"]');
			if (!next || next.disabled) break;
			next.click();
			await settle();
			for (const one of tiles()) seen.add(one);
		}

		expect(pages).toBeGreaterThan(1);
		expect(seen.size).toBe(LENGTH);
		// The route names no row, so the grid must never continue after one.
		expect(asked.reads.every((query) => !('after' in query))).toBe(true);
	}, 20_000);

	it('returns by Previous to the page Next left, whatever shapes its files are', async () => {
		asked.mixed = true;
		await open();
		const turn = async (label: string) => {
			host!.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!.click();
			await settle();
			return tiles()[0];
		};

		const first = tiles()[0];
		const second = await turn('Next page');
		await turn('Next page');

		expect([await turn('Previous page'), await turn('Previous page')]).toEqual([second, first]);
	}, 20_000);

	it('offers no move: a collection has no order of its own', async () => {
		await open();
		expect(await menuWords()).not.toContain('Move earlier');
		expect(await menuWords()).not.toContain('Move later');
	});

	it('is asked in the Sort by order, pinned first, and a new order starts at the top', async () => {
		await open();
		const offered = screenBar.tools.sorts;
		expect(Array.isArray(offered) && offered.map((one) => one.value)).toContain('name_az');
		expect(screenBar.tools.sort).toBe('newest');
		expect(asked.reads[0]).toMatchObject({ sort: 'newest', pinned_first: '1' });

		host!.querySelector<HTMLButtonElement>('button[aria-label="Next page"]')!.click();
		await settle();
		screenBar.tools.onSort!('name_az');
		await settle();
		expect(asked.reads.at(-1)).toMatchObject({ sort: 'name_az', offset: 0 });
	});
});
