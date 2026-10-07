/*
 * "Select 1,000 of 1,200", and what the verb pressed after it actually sends.
 *
 * Picking the whole question reads every id, and the verb must send all of them, not only the rows
 * the page is drawing; the bar and the server must agree on the number. This shows only on a
 * library bigger than a page.
 *
 * Asserted on what is sent, like the filtering and live suites next door: the request is the
 * behaviour and the one part a test without a browser can see. The 500-at-a-time splitting is
 * asserted here too, since only a whole-question pick is big enough to reach a second chunk.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { LOOP_SOURCE, MOST_SELECTED } from '$lib/grid/grid.svelte';

/*
 * A library of this many files, answered a page at a time as the server does.
 *
 * Bigger than the ceiling on one press, deliberately, to put three things under test that a small
 * library hides: the reader pages (six requests of two hundred), the pick stops at the announced
 * ceiling rather than going further, and the write that follows is split into more than one chunk.
 */
const LIBRARY = 1200;

const asked = vi.hoisted(() => ({
	reads: [] as Record<string, unknown>[],
	/** Every batch of ids a bulk write was given, in the order the chunks went out. */
	favorited: [] as string[][]
}));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets' || path === '/loops') {
				const query = options?.query ?? {};
				asked.reads.push({ path, ...query });
				const offset = Number(query.offset ?? 0);
				const limit = Number(query.limit ?? 50);
				const count = Math.max(0, Math.min(limit, LIBRARY - offset));
				/* A wall of MOMENTS: the row has an identity of its own and the file it was cut from
				   is a field on it. That is the one wall where a row id is not a file id, and it is
				   why the whole-query read hands back rows rather than ids. */
				const mark = path === '/loops';
				return {
					items: Array.from({ length: count }, (_, each) => ({
						id: mark ? `m${offset + each}` : `a${offset + each}`,
						...(mark ? { asset_id: `a${offset + each}`, start_ms: 0, end_ms: 4000 } : {}),
						media_type: 'video',
						width: 1920,
						height: 1080,
						duration_ms: 4000,
						thumb: true,
						favorite: false,
						rating: 0,
						concealed: false,
						shared: false
					})),
					total: LIBRARY,
					limit,
					offset,
					complete: true
				};
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		post: vi.fn(async (path: string, options?: { body?: Record<string, unknown> }) => {
			if (path === '/assets/favorite') {
				asked.favorited.push((options?.body?.asset_ids ?? []) as string[]);
			}
			return { changed: 0, skipped: 0, reason: null };
		}),
		put: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

/* A BOX WITH A SIZE, because jsdom gives every element none and the wall lays itself out against
   the width it measures, so without this the grid draws a page of zero rows and there is no tile
   to pick. The numbers are an ordinary window; nothing here depends on which. */
function sized(width: number, height: number): () => void {
	const was = {
		w: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth'),
		h: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
	};
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => width
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => height
	});
	return () => {
		if (was.w) Object.defineProperty(HTMLElement.prototype, 'clientWidth', was.w);
		if (was.h) Object.defineProperty(HTMLElement.prototype, 'clientHeight', was.h);
	};
}

let unsize: (() => void) | null = null;

beforeEach(() => {
	asked.reads = [];
	asked.favorited = [];
	unsize = sized(1200, 900);
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	unsize?.();
	unsize = null;
});

async function settle() {
	flushSync();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
}

/** Draw the grid the way Browse does, and let its start-up settle. */
async function grid(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here', ...extra }
	});
	await settle();
	asked.reads = [];
}

/** Every tile the wall has drawn. */
function tiles(): HTMLElement[] {
	return [...host.querySelectorAll('.placed')] as HTMLElement[];
}

/** Start a selection, which is what puts the bar on screen. Ctrl, because a plain click opens. */
function pickOne(): string[] {
	const drawn = tiles();
	expect(drawn.length, 'the wall drew no tiles at all').toBeGreaterThan(0);
	drawn[0].dispatchEvent(
		new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true })
	);
	flushSync();
	return drawn.map((tile) => tile.getAttribute('data-tile') ?? '');
}

/** A button on the selection bar, by its words. */
function barButton(words: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('.bar button')].find((one) =>
		(one.textContent ?? '')
			.replace(/[\uE000-\uF8FF]/g, '')
			.trim()
			.startsWith(words)
	);
	if (!(found instanceof HTMLButtonElement)) {
		throw new Error(`no bar button saying ${words}, the bar says ${barWords()}`);
	}
	return found;
}

function barWords(): string {
	return (host.querySelector('.bar')?.textContent ?? '(no bar)')
		.replace(/[\uE000-\uF8FF]/g, '')
		.trim();
}

/** Press the bar's offer and wait until the whole question has actually been read. */
async function pickTheWholeQuery(): Promise<void> {
	barButton('Select').click();
	await vi.waitFor(() => {
		flushSync();
		// It says "Selecting..." while the pages are coming in. Asserting before that is over
		// reads the count of the pick somebody started from, which is one.
		if (!barWords().includes(`${MOST_SELECTED.toLocaleString()} files selected`)) {
			throw new Error(`still reading: the bar says ${barWords()}`);
		}
	});
}

// Each of these picks and then acts on the largest pick the server allows, thousands of rows
// through the grid, which can run past the default five seconds on a two-processor runner.
vi.setConfig({ testTimeout: 30_000 });

describe('picking the whole question and then acting on it', () => {
	it('sends every id the pick was made of, not the page that is loaded', async () => {
		await grid();
		const onPage = pickOne();
		expect(barWords()).toContain('1 file selected');
		// The offer states the ceiling, because the query is bigger than it.
		expect(barButton('Select').textContent).toContain(MOST_SELECTED.toLocaleString());

		await pickTheWholeQuery();

		barButton('Add to favorites').click();
		await vi.waitFor(() => {
			flushSync();
			if (asked.favorited.flat().length < MOST_SELECTED) throw new Error('not all sent yet');
		});

		const sent = asked.favorited.flat();
		// EXACTLY what the bar said, which is the whole of it: a count on screen and a list on the
		// wire that came from two different questions is what this test exists for.
		expect(sent).toHaveLength(MOST_SELECTED);
		expect(sent[0]).toBe('a0');
		expect(sent[MOST_SELECTED - 1]).toBe(`a${MOST_SELECTED - 1}`);
		// And it genuinely reached past the wall, or this would pass on the fault it was written for.
		expect(onPage.length).toBeLessThan(MOST_SELECTED);
	});

	it('splits the write at the server cap rather than sending one enormous request', async () => {
		// 500 is the kernel's own ceiling, which `overChunks` splits at. Only a whole-question pick
		// is big enough to send a second chunk from a real selection.
		await grid();
		pickOne();
		await pickTheWholeQuery();

		barButton('Add to favorites').click();
		await vi.waitFor(() => {
			flushSync();
			if (asked.favorited.flat().length < MOST_SELECTED) throw new Error('not all sent yet');
		});

		expect(asked.favorited.length).toBeGreaterThan(1);
		for (const chunk of asked.favorited) expect(chunk.length).toBeLessThanOrEqual(500);
	});

	it('names the FILE on a wall of moments, for the rows the page never held', async () => {
		/* The one wall where a row is not a file. The verbs are handed files, which this screen reads
		   off the page it is drawing, and a whole-query pick is by definition mostly rows the page
		   does not hold, so without the files being recorded as they are read, a thousand MARK ids
		   would have gone to a route that takes file ids. Nothing would have failed loudly: the
		   server would have answered that nothing changed. */
		await grid({ source: LOOP_SOURCE });
		pickOne();
		await pickTheWholeQuery();

		barButton('Add to favorites').click();
		await vi.waitFor(() => {
			flushSync();
			if (asked.favorited.flat().length < MOST_SELECTED) throw new Error('not all sent yet');
		});

		const sent = asked.favorited.flat();
		expect(sent[0]).toBe('a0');
		expect(sent[MOST_SELECTED - 1]).toBe(`a${MOST_SELECTED - 1}`);
		// The rows were genuinely marks, or this passes on a wall where the two are the same string.
		expect(sent.some((id) => id.startsWith('m'))).toBe(false);
	});

	it('a pick made on the wall still sends only what was picked', async () => {
		// The other half of the branch. Without this, a reader that simply sent every id it had
		// would pass the tests above and be wrong about every ordinary selection in the application.
		await grid();
		pickOne();

		barButton('Add to favorites').click();
		await settle();

		expect(asked.favorited.flat()).toEqual(['a0']);
	});
});
