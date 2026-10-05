import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import codepoints from '$lib/generated/icon-codepoints.json';

/*
 * The bar over every wall, and the seam that decides what its panel is editing.
 *
 * The bar reads the address, the navigation module and three stores, and its panel reads and writes
 * either the address or a kept filter's draft (`Narrowing`): every column, every tick and every
 * chip goes through that one choice, and this file tests it.
 *
 * What is pinned is which filter each thing writes to: the bar's own chips write the address
 * whatever else is happening, and the panel writes the draft while an edit is open. Both directions
 * are asserted, because either backwards is invisible on screen until somebody loses their filters.
 * Layout, hover and the drawer's animation cannot be tested here: jsdom has none of them.
 *
 * The mocks: `$app/state` hands the component a real `URL`, so the address is a value this file
 * sets rather than a router it drives. `goto` is a spy, and what it was called with is the
 * assertion, because a navigation is how this component says "the screen should now be filtered by
 * that".
 */

const held = vi.hoisted(() => ({ url: new URL('http://x/browse'), gone: [] as string[] }));

vi.mock('$app/state', () => ({
	get page() {
		return { url: held.url };
	}
}));
vi.mock('$app/navigation', () => ({
	goto: vi.fn((where: URL | string) => {
		held.gone.push(String(where));
		return Promise.resolve();
	})
}));
/*
 * The facet counts answer with a real value, because the panel's own write path is reachable only
 * through a row somebody can tick, and that path is the seam this file exists for. A mock returning
 * `[]` would draw nothing to click and leave the whole of it unexercised.
 */
vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			/* Any noun's facet route, not the file one alone: the panel asks `/people/facets` on a
			   wall of people now, and a mock that answered only files would leave every column on
			   that wall empty, and an empty column draws nothing to click, which is the half of
			   this file that matters. */
			if (path.endsWith('/facets')) {
				/*
				 * One column answers with the word under test and every other with its own: the row
				 * helper below finds the first column holding a word, so a single word for all
				 * would make every assertion about a hair colour land on the Gender column beside
				 * it.
				 */
				const facet = String(options?.query?.facet ?? '');
				const word =
					path === '/assets/facets'
						? 'runway'
						: facet === 'hair_color'
							? 'BLONDE'
							: `only-${facet}`;
				return { facet, values: [{ value: word, count: 3 }] };
			}
			/* A Site or a tag read by id, which is where a chip asks a name nobody counted. */
			if (/^\/(sites|tags)\/[0-9A-Z]{26}$/.test(path)) return { name: 'Harbour Network' };
			return { items: [], text: '', clauses: [], terms: {}, problems: [] };
		}),
		post: vi.fn(async () => ({})),
		patch: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {}
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

const FilterBar = (await import('./FilterBar.svelte')).default;
const { goto } = await import('$app/navigation');
const { api } = await import('$lib/api/client');
const { screenBar, FILTERS_PANEL, PHONE_SHEET } = await import('./screen-bar.svelte');
const { phoneWidth } = await import('$lib/components/common/phone-width.svelte');
const { savedSearches } = await import('$lib/search/saved-searches.svelte');

let host: HTMLElement | undefined;
let drawn: Record<string, unknown> | undefined;
const owner = Symbol('a screen');

/** The bar, on a screen that offers filters, filtered by whatever `at` says. */
function draw(at = '') {
	held.url = new URL(`http://x/browse${at}`);
	held.gone.length = 0;
	screenBar.publish(owner, { filterable: true });
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
	flushSync();
}

/** The same bar over a wall of PEOPLE, which filters its own rows rather than the files on them. */
function drawOverPeople(at = '') {
	held.url = new URL(`http://x/people${at}`);
	held.gone.length = 0;
	screenBar.publish(owner, { filterable: true, subject: 'person' });
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
	flushSync();
}

/** The bar over a card tab of an entity page: the wall is not files, but the address may carry
 *  the filters a picked tile wrote for the Files tab. */
function drawOverACardTab(at = '') {
	held.url = new URL(`http://x/people/p1${at}`);
	held.gone.length = 0;
	screenBar.publish(owner, { filterable: 'This wall is seen with, not files' });
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
	flushSync();
}

/** The chips on the ROW, which describe the screen. Never the ones in the panel.
 *  Anything outside plain ASCII goes first: a chip's text carries the cross's own ligature, which is
 *  not a space and does not trim. */
function barChips(): string[] {
	return [...(host?.querySelectorAll('.filters .chip') ?? [])].map((one) =>
		(one.textContent ?? '')
			.replace(/[^\x20-\x7E]/g, '')
			.replace(/\s+/g, ' ')
			.trim()
	);
}

/** Open the panel, which is where the columns, the kept filters and any edit are drawn.
 *  The counts arrive over a promise, so the columns need a turn of the loop before they have rows. */
async function openPanel() {
	screenBar.show(FILTERS_PANEL);
	flushSync();
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
}

/** One value in a column, by the word on it. The whole row is the target. See `FacetPanel`. */
function columnRow(word: string): HTMLElement | undefined {
	return [...(host?.querySelectorAll<HTMLElement>('.column .value') ?? [])].find((one) =>
		one.textContent?.includes(word)
	);
}

beforeEach(() => {
	savedSearches.items = [
		{ id: 'kept', name: 'Runway clips', query: 'tags=runway&people=Anna', kind: 'asset' },
		{ id: 'other', name: 'PMV', query: 'tags=PMV', kind: 'asset' },
		/* Kept on the People wall. It is in the same list and belongs to a different screen, which
		   is what the two tests at the foot of this file are about. */
		{ id: 'theirs', name: 'Redheads', query: 'hair_color=RED', kind: 'person' }
	];
	savedSearches.loaded = true;
	savedSearches.editing = null;
	/* What was asked for is a per-test fact. Left to accumulate, a test searching the calls for one
	   column finds the one an EARLIER test made, which is how an assertion about the question this
	   bar asks comes to be made against a question a different screen asked. */
	vi.mocked(api.get).mockClear();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	host = undefined;
	screenBar.release(owner);
	screenBar.close();
	savedSearches.items = [];
	savedSearches.loaded = false;
	savedSearches.editing = null;
	localStorage.clear();
});

describe('what the row says', () => {
	it('draws a chip for every named filter in the address', () => {
		draw('?tags=beach&media=video');

		expect(barChips()).toEqual(['tags: beach', 'media: Videos']);
	});

	it('draws no row at all when nothing is narrowing the screen', () => {
		/* An empty row that keeps its ground is a slab above every unfiltered screen, which is most of
		   them. It is `display: none`, so what is asserted is the class the rule keys on. */
		draw();

		expect(host?.querySelector('.bar')?.className).toContain('nothing');
	});

	/* A narrow window (a desktop window snapped to half the screen) moves Filter and Sort off the
	   top bar and onto this row. An otherwise empty row must still be drawn then, or the two
	   controls are nowhere at all. */
	it('draws the row when the top bar has no room for the menus, with the menus on it', () => {
		screenBar.roomOnTopBar = false;
		try {
			draw();

			const bar = host?.querySelector('.bar');
			expect(bar?.className).not.toContain('nothing');
			expect(bar?.querySelector('[aria-label="Filter"]')).not.toBeNull();
		} finally {
			screenBar.roomOnTopBar = true;
		}
	});

	it('names the screen after a kept filter when it is exactly one of them', () => {
		/* Compared rather than remembered from the press. See `sameFilters`. The order the address
		   happens to be written in must not matter. */
		draw('?people=Anna&tags=runway');

		expect(barChips()[0]).toContain('Runway clips');
	});

	it('stops naming it the moment one of its filters changes', () => {
		draw('?people=Anna&tags=runway&rating=8');

		expect(barChips().some((one) => one.includes('Runway clips'))).toBe(false);
	});
});

describe('which narrowing a chip writes to', () => {
	/*
	 * The seam, and the reason this file was written. Getting either direction backwards is invisible
	 * until somebody loses the filters they were looking at.
	 */
	it("the row's own cross navigates, because the row describes the SCREEN", () => {
		draw('?tags=beach&media=video');

		host?.querySelector<HTMLElement>('.filters .chip .remove')?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		expect(held.gone[0]).toContain('media=video');
		expect(held.gone[0]).not.toContain('tags=beach');
	});

	it('the row goes on describing the screen while a kept filter is being edited', async () => {
		/*
		 * The screen's chips are shown during an edit: those under "Editing" are the filter's and
		 * these are the screen's, and an edit does not change the screen.
		 */
		draw('?tags=beach&media=video');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway&people=Anna',
			kind: 'asset'
		};
		flushSync();

		expect(barChips()).toEqual(['tags: beach', 'media: Videos']);
	});

	it("the row's cross still navigates WHILE an edit is open", async () => {
		/*
		 * The chips on this row are handed `address` explicitly rather than the seam, and this says
		 * so. Pointed at the seam they would edit the draft while an edit was open, so taking a
		 * filter off the screen would silently rewrite the filter being edited and leave the screen
		 * as it was. With only chip-in-the-edit and column presses, that change would be invisible
		 * to every other test.
		 */
		draw('?tags=beach&media=video');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway',
			kind: 'asset'
		};
		flushSync();

		host?.querySelector<HTMLElement>('.filters .chip .remove')?.click();
		flushSync();

		expect(held.gone, 'taking a chip off the ROW did not navigate').toHaveLength(1);
		expect(held.gone[0]).toContain('media=video');
		expect(savedSearches.editing?.draft, 'the row wrote the draft instead').toBe('tags=runway');
	});

	it('an edit writes the DRAFT and never the address', async () => {
		/* The whole change. Ticking a column, or taking a chip off the edit, has to leave the screen
		   exactly where it was, so `goto` must not be called at all. */
		draw('?tags=beach');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway&people=Anna',
			kind: 'asset'
		};
		flushSync();

		const editing = host?.querySelector('[aria-label="Editing a saved filter"]');
		expect(editing, 'the edit is not drawn, so nothing below it means anything').not.toBeNull();
		editing?.querySelector<HTMLElement>('.chip .remove')?.click();
		flushSync();

		expect(held.gone, 'the screen was navigated during an edit').toHaveLength(0);
		expect(savedSearches.editing?.draft).toBe('people=Anna');
		expect(barChips()).toEqual(['tags: beach']);
	});

	it('leaves the address alone when an edit is cancelled', async () => {
		draw('?tags=beach');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway',
			kind: 'asset'
		};
		flushSync();

		const cancel = [...(host?.querySelectorAll('button') ?? [])].find((one) =>
			one.textContent?.includes('Cancel')
		);
		cancel?.click();
		flushSync();

		expect(savedSearches.editing).toBeNull();
		expect(held.gone).toHaveLength(0);
		expect(barChips()).toEqual(['tags: beach']);
	});
});

describe('one chip per value', () => {
	/*
	 * Two tags ticked in one column are one parameter, `tags=runway|Edited`, and one chip for it,
	 * "runway or Edited", could only refuse both, so nothing could keep one tag and refuse the
	 * other, on the bar or in a kept filter's editor. A chip per value is what a press refuses alone.
	 */
	function tagsIn(address: string): string[] {
		return new URL(address, 'http://x').searchParams.getAll('tags');
	}

	it('draws each value of one dimension as its own chip', () => {
		draw('?tags=runway|Edited');
		expect(barChips()).toEqual(['tags: runway', 'tags: Edited']);
	});

	it('refuses the one value pressed and keeps the other', () => {
		draw('?tags=runway|Edited');
		host?.querySelectorAll<HTMLElement>('.filters .chip .body')[1]?.click();
		flushSync();
		expect(tagsIn(held.gone[0])).toEqual(['runway', '-Edited']);
	});

	it('refuses a value where its facet stood, ahead of the facets after it', () => {
		draw('?tags=a|b&people=c');
		host?.querySelector<HTMLElement>('.filters .chip .body')?.click();
		flushSync();
		expect([...new URL(held.gone[0], 'http://x').searchParams]).toEqual([
			['tags', 'b'],
			['tags', '-a'],
			['people', 'c']
		]);
		expect(vi.mocked(goto).mock.lastCall?.[1]).toEqual({ keepFocus: true });
	});

	it('says a presence filter whole, and a press swaps Has for No', () => {
		draw('?tags=any');
		expect(barChips()).toEqual(['tags: Has tags']);
		host?.querySelector<HTMLElement>('.filters .chip .body')?.click();
		flushSync();
		expect(tagsIn(held.gone[0])).toEqual(['none']);
	});

	it('keeps a value refused on its own when any-or-all is switched', () => {
		draw('?tags=runway|Edited&tags=-PMV');
		host?.querySelector<HTMLElement>('.filters .chip .aside button')?.click();
		flushSync();
		expect(tagsIn(held.gone[0])).toEqual(['runway,Edited', '-PMV']);
	});

	it('refuses one value of a kept filter in its editor, and Save would keep the refusal', async () => {
		draw('?tags=beach');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway|Edited',
			kind: 'asset'
		};
		flushSync();

		const editing = host?.querySelector('[aria-label="Editing a saved filter"]');
		expect(editing?.querySelectorAll('.chip')).toHaveLength(2);
		editing?.querySelectorAll<HTMLElement>('.chip .body')[1]?.click();
		flushSync();

		expect(held.gone, 'the screen was navigated during an edit').toHaveLength(0);
		expect(tagsIn(`?${savedSearches.editing?.draft}`)).toEqual(['runway', '-Edited']);
	});
});

describe('the panel the row drops open', () => {
	it('is drawn once the bar says it is open', async () => {
		draw('?tags=beach');
		await openPanel();

		expect(host?.querySelector('.bar-panel')).not.toBeNull();
	});
});

/*
 * At a phone's width Filter and Sort are one control on the top bar, and it opens THIS: a sheet
 * from the bottom edge holding the orders and the facets, the same panel the desktop's drawer holds.
 */
describe("the phone's sheet", () => {
	afterEach(() => {
		phoneWidth.yes = false;
	});

	/** The bar at a phone's width over a wall offering what `tools` says, with the sheet open. */
	async function openSheet(tools: Record<string, unknown>) {
		phoneWidth.yes = true;
		held.url = new URL('http://x/browse?tags=beach');
		held.gone.length = 0;
		screenBar.publish(owner, tools);
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
		screenBar.show(PHONE_SHEET);
		flushSync();
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		return host.querySelector('aside.drawer') as HTMLElement;
	}

	const ORDERS = [
		{ value: 'newest', label: 'Newest first' },
		{ value: 'oldest', label: 'Oldest first' }
	];

	it('holds the orders and the facets, and a tick in it filters the screen', async () => {
		const sheet = await openSheet({ filterable: true, sorts: ORDERS, sort: 'newest' });

		expect(sheet, 'no sheet').not.toBeNull();
		expect(sheet.classList.contains('open')).toBe(true);
		expect(sheet.getAttribute('aria-label')).toBe('Filter and sort');
		expect(sheet.querySelector('[aria-label="Sort by"]'), 'no orders in it').not.toBeNull();
		expect(sheet.querySelector('.bar-panel'), 'no facets in it').not.toBeNull();

		const row = [...sheet.querySelectorAll<HTMLElement>('.column .value')].find((one) =>
			one.textContent?.includes('runway')
		);
		expect(row, 'no column drew a value to tick').toBeTruthy();
		row?.click();
		flushSync();
		expect(decodeURIComponent(held.gone[0] ?? '')).toContain('runway');
	});

	it('holds only the orders on a screen that orders and does not filter', async () => {
		const sheet = await openSheet({ filterable: 'Nothing to filter here', sorts: ORDERS });

		expect(sheet.querySelector('[aria-label="Sort by"]')).not.toBeNull();
		expect(sheet.querySelector('.bar-panel')).toBeNull();
	});

	it('shuts on Escape, and the bar hears it', async () => {
		await openSheet({ filterable: true, sorts: ORDERS });
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		expect(screenBar.open).toBeNull();
	});

	it('is not drawn at all on a desktop window, where the drawer is the frame', async () => {
		await openSheet({ filterable: true, sorts: ORDERS });
		phoneWidth.yes = false;
		flushSync();
		expect(host?.querySelector('aside.drawer')).toBeNull();
	});
});

describe('the columns, which are the half that writes through the seam', () => {
	/*
	 * Every tick in a column goes through `narrowing`, while a chip in the edit does not (the host
	 * hands those the draft directly), so a test pressing only chips would leave the seam
	 * untouched.
	 */
	it('ticking a column NAVIGATES while nothing is being edited', async () => {
		draw('?tags=beach');
		await openPanel();

		const row = columnRow('runway');
		expect(row, 'no column drew a value to tick').toBeTruthy();
		row?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		expect(decodeURIComponent(held.gone[0])).toContain('runway');
	});

	it('ticking a column writes the DRAFT while one is, and leaves the screen alone', async () => {
		draw('?tags=beach');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'people=Anna',
			kind: 'asset'
		};
		flushSync();

		const row = columnRow('runway');
		expect(row, 'no column drew a value to tick').toBeTruthy();
		row?.click();
		flushSync();

		expect(held.gone, 'the screen was navigated by a tick during an edit').toHaveLength(0);
		expect(savedSearches.editing?.draft).toContain('runway');
		expect(barChips()).toEqual(['tags: beach']);
	});

	it('a column reads its three states from the DRAFT while one is being edited', async () => {
		/* The read half of the same seam. Read from the address, a column would show the screen's
		   choices while somebody was editing a filter that does not hold them. */
		draw('?tags=beach');
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway',
			kind: 'asset'
		};
		flushSync();

		expect(columnRow('runway')?.className).toContain('on');
	});
});

/*
 * THE SAME BAR OVER A WALL THAT IS NOT FILES.
 *
 * The panel's columns belong to the noun now, and so do two things this file owns: which parameters
 * in the address are FILTERS, and how a pick is spelled. The second is the one that cannot be seen
 * on screen and is the reason these are here: the entity routes read a repeated key as "either of
 * these" and know nothing about the pipe the file route uses for the same thing, so a pick written
 * in the file's grammar would produce an address that quietly matches nothing at all.
 */
describe('a wall of people, which narrows its own rows', () => {
	it('writes each picked value as its OWN parameter', async () => {
		drawOverPeople();
		await openPanel();

		const row = columnRow('Blonde');
		expect(row, 'no column drew a value to tick').toBeTruthy();
		row?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		expect(decodeURIComponent(held.gone[0])).toContain('hair_color=BLONDE');
	});

	it('a second value WIDENS the column rather than replacing the first', async () => {
		/* Repeated keys are "either of these" to these routes. Written the file wall's way (one
		   parameter holding `BLONDE|RED`), the server would look for a hair colour spelled with a
		   pipe in it and the wall would empty itself the moment somebody used the column twice. */
		drawOverPeople('?hair_color=RED');
		await openPanel();

		columnRow('Blonde')?.click();
		flushSync();

		const gone = decodeURIComponent(held.gone[0] ?? '');
		expect(gone).toContain('hair_color=RED');
		expect(gone).toContain('hair_color=BLONDE');
		expect(gone, 'the values were joined the file wall s way').not.toContain('|');
	});

	it('gives a row TWO states here too, off and on', async () => {
		/* Off, on, off: the one click model every wall shares. Refusing is the chip's verb. */
		drawOverPeople('?hair_color=BLONDE');
		await openPanel();

		expect(columnRow('Blonde')?.className, 'the value did not read as chosen').toContain('on');
		columnRow('Blonde')?.click();
		flushSync();

		expect(decodeURIComponent(held.gone[0] ?? '')).not.toContain('hair_color');
	});

	it('draws a chip for a facet of the noun, and none for how the wall is PAGED', () => {
		/* The order, the anchor and the page size are in that address too. A chip for one of those
		   would offer a cross that changes where the wall starts rather than what is on it. */
		drawOverPeople('?hair_color=BLONDE&sort=largest&from=01ABC&offset=60');

		expect(barChips()).toEqual(['Hair color: Blonde']);
	});

	it("offers the filters kept HERE, and none of the library's", () => {
		/*
		 * A wall of people draws its own kept filters, because a filter records which wall it was
		 * kept on. The negative half still holds: a file filter offered here would be a pill that
		 * changes the link and moves nothing.
		 */
		drawOverPeople();
		screenBar.show(FILTERS_PANEL);
		flushSync();

		expect(host?.querySelector('.bar-panel'), 'the panel is not drawn at all').not.toBeNull();
		expect(host?.textContent).toContain('Saved filters');
		expect(host?.textContent).toContain('Redheads');
		expect(host?.textContent, 'a filter about files was offered here').not.toContain(
			'Runway clips'
		);
	});

	it('still offers them over a wall of FILES, which is where they began', async () => {
		/* The other side of the same key: the library draws the filters kept on the library, and the
		   People one is not among them. */
		draw('?tags=beach');
		await openPanel();

		expect(host?.textContent).toContain('Saved filters');
		expect(host?.textContent).toContain('Runway clips');
		expect(host?.textContent, 'a filter about people was offered over files').not.toContain(
			'Redheads'
		);
	});
});

/*
 * ONE CLICK MODEL ON EVERY WALL.
 *
 * The same panel sits over the library and over each entity wall, so a row has to answer a click
 * the same way on all of them: the first click picks it, the second takes it off. A second click
 * that turned the row into a refusal on some walls and not on others made one control two.
 */
describe('a second click on a row, on every wall', () => {
	/** The bar over `path`, showing `subject`, at the address `at`. */
	function drawAt(path: string, subject: 'asset' | 'person' | 'site' | 'tag', at = '') {
		if (drawn) unmount(drawn);
		host?.remove();
		held.url = new URL(`http://x${path}${at}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, subject });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	/** The first value in the first column that has one. */
	function firstRow(): HTMLElement | undefined {
		return host?.querySelector<HTMLElement>('.column .value') ?? undefined;
	}

	const WALLS = [
		['/browse', 'asset'],
		['/people', 'person'],
		['/sites', 'site'],
		['/tags', 'tag']
	] as const;

	for (const [path, subject] of WALLS) {
		it(`picks, then takes off, and never refuses, on ${path}`, async () => {
			drawAt(path, subject);
			await openPanel();
			const row = firstRow();
			expect(row, 'no column drew a value to click').toBeTruthy();
			row?.click();
			flushSync();
			const once = new URL(held.gone[0] ?? '', 'http://x');
			const picked = [...once.searchParams.entries()].filter(([name]) => name !== 'offset');
			expect(picked, 'the first click picked nothing').toHaveLength(1);
			expect(picked[0][1].startsWith('-'), 'the first click refused the value').toBe(false);

			/* The screen now carries the pick, as it would after the navigation. */
			drawAt(path, subject, once.search);
			await openPanel();
			expect(firstRow()?.className, 'the pick did not read as chosen').toContain('on');
			firstRow()?.click();
			flushSync();

			const twice = new URL(held.gone[0] ?? '', 'http://x');
			expect(twice.searchParams.has(picked[0][0]), 'the second click kept the value').toBe(false);
			expect(decodeURIComponent(twice.search), 'the second click refused it').not.toMatch(/=-/);
		});
	}

	it('takes a refused value off with one click, rather than cycling it back on', async () => {
		/* A refusal is made on the chip; clicking the red row is somebody taking it back. */
		drawAt('/browse', 'asset', '?media=-runway');
		await openPanel();
		expect(firstRow()?.className, 'the refusal did not read as refused').toContain('out');
		firstRow()?.click();
		flushSync();

		expect(decodeURIComponent(held.gone[0] ?? '')).not.toContain('runway');
	});
});

describe('what the columns are counted against', () => {
	it('carries EVERY value of another column, not just the last one written', async () => {
		/*
		 * A parameter may appear more than once (`?people=anna&people=-beth` is "Anna, and not
		 * Beth"), and a question built with `Object.fromEntries` keeps the last entry of a repeated
		 * name. Every other column would then be counted against the refusal alone: numbers that look
		 * plausible in every screenshot and are wrong in all of them. The entity walls write every
		 * multi-value pick as repeats, so this is the ordinary case there rather than a corner of it.
		 */
		draw('?people=anna&people=-beth');
		await openPanel();

		const asked = vi
			.mocked(api.get)
			.mock.calls.find(
				([path, options]) =>
					path === '/assets/facets' &&
					(options as { query?: Record<string, unknown> })?.query?.facet === 'tags'
			);
		expect(asked, 'no column was counted at all, so this proves nothing').toBeTruthy();
		expect((asked?.[1] as { query?: Record<string, unknown> })?.query?.people).toEqual([
			'anna',
			'-beth'
		]);
	});
});

it('draws the chips a picked tile wrote, on a card tab whose wall is not files', () => {
	/*
	 * The bar shows the picks as chips over Seen with: the picks are in the address, and the chips
	 * say they are in force.
	 */
	drawOverACardTab('?show=people&people=Jane+Else');

	expect(barChips()).toEqual(['people: Jane Else']);
});

describe('the username a wall is narrowed to', () => {
	/* `?username=` is not a word of the query language (the address carries the username's id) so
	   it is not one of the named chips, and without its own it would be a filter in force that nothing
	   on the row said. The words are the server's page answer, published by the wall. */
	const esmewrenfield = { id: 'ac1', username: 'esmewrenfield', site: 'SomeSite', person_id: null };

	function drawNarrowed(at: string, username: typeof esmewrenfield | { person_id: string } | null) {
		held.url = new URL(`http://x/browse${at}`);
		held.gone.length = 0;
		screenBar.publish(owner, {
			filterable: true,
			username: username === null ? null : { ...esmewrenfield, ...username }
		});
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	it('says the username and its site, from what the page answered', () => {
		drawNarrowed('?username=ac1', esmewrenfield);

		expect(barChips()).toEqual(['username: @esmewrenfield on SomeSite']);
		// Nobody is behind it, so the chip goes nowhere: a plain label with a cross.
		expect(host?.querySelector('.filters a')).toBeNull();
	});

	it('opens the person behind the username when there is one', () => {
		drawNarrowed('?username=ac1', { person_id: 'p9' });

		expect(host?.querySelector<HTMLAnchorElement>('.filters a')?.getAttribute('href')).toBe(
			'/people/p9'
		);
	});

	it('takes the username off on its cross, and leaves every other filter where it was', () => {
		drawNarrowed('?username=ac1&tags=beach', esmewrenfield);

		expect(barChips()[0]).toBe('username: @esmewrenfield on SomeSite');
		host?.querySelector<HTMLElement>('.filters .chip .remove')?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		expect(held.gone[0]).not.toContain('username=');
		expect(held.gone[0]).toContain('tags=beach');
	});

	it('is still drawn, as "a username", before the page has named it', () => {
		/* The filter is in force whether or not it can be named: a username this viewer may not be
		   shown is named nowhere, and a page still answering for the last username must not name this
		   one. The cross is the way out in both cases. */
		drawNarrowed('?username=ac2', esmewrenfield);

		expect(barChips()).toEqual(['username: a username']);
		expect(host?.querySelector('.filters a')).toBeNull();
	});
});

describe('the folder a wall is narrowed to by its id', () => {
	/* A History line's folder link writes `?in=<folder id>`, and the chip names the folder rather
	   than drawing the id. The name is asked of the folder's own scoped read; one this viewer may
	   not see is "a folder". */
	const FOLDER = '01J5T6R7S8FGH1JKMNPQRSTVW2';

	function answeringFolders(names: Record<string, string>) {
		const usual = vi.mocked(api.get).getMockImplementation()!;
		vi.mocked(api.get).mockImplementation(async (path, options) => {
			const id = String(path).match(/^\/library\/folders\/(.+)$/)?.[1];
			if (id !== undefined) {
				if (id in names) return { name: names[id] } as never;
				throw new Error('not found');
			}
			return usual(path, options);
		});
		return () => vi.mocked(api.get).mockImplementation(usual);
	}

	async function settle() {
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
	}

	it('names the folder rather than drawing its id', async () => {
		const restore = answeringFolders({ [FOLDER]: 'Holiday' });
		draw(`?in=${FOLDER}`);
		await settle();

		expect(barChips()).toEqual(['in: Holiday']);
		restore();
	});

	it('says "a folder" where the folder cannot be named', async () => {
		const restore = answeringFolders({});
		draw(`?in=${FOLDER}`);
		await settle();

		expect(barChips()).toEqual(['in: a folder']);
		restore();
	});

	it('draws a typed path as written, since it is words already', async () => {
		const restore = answeringFolders({});
		draw('?in=Downloads');
		await settle();

		expect(barChips()).toEqual(['in: Downloads']);
		restore();
	});

	it('takes the folder off on its cross, and leaves every other filter where it was', async () => {
		const restore = answeringFolders({ [FOLDER]: 'Holiday' });
		draw(`?in=${FOLDER}&tags=beach`);
		await settle();

		const chip = [...(host?.querySelectorAll<HTMLElement>('.filters .chip') ?? [])].find((one) =>
			(one.textContent ?? '').includes('Holiday')
		);
		chip?.querySelector<HTMLElement>('.remove')?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		expect(held.gone[0]).not.toContain('in=');
		expect(held.gone[0]).toContain('tags=beach');
		restore();
	});
});

describe('the files of one History line', () => {
	/* A History line's count opens `?filed=<site>~<source>~<day>`: exactly the files it counted.
	   Not a word of the query language, so it has its own chip, in words, with the Site's name asked
	   of the Site's own scoped read. */
	function answeringSites(names: Record<string, string>) {
		const usual = vi.mocked(api.get).getMockImplementation()!;
		vi.mocked(api.get).mockImplementation(async (path, options) => {
			const id = String(path).match(/^\/sites\/(.+)$/)?.[1];
			if (id !== undefined) {
				if (id in names) return { name: names[id] } as never;
				throw new Error('not found');
			}
			return usual(path, options);
		});
		return () => vi.mocked(api.get).mockImplementation(usual);
	}

	async function settle() {
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
	}

	it('says the Site, who filed and the day, and opens the Site where the line is', async () => {
		const restore = answeringSites({ s1: 'Instagram' });
		draw('?filed=s1~download~2023-11-14');
		await settle();

		expect(barChips()).toEqual(['filed under: Instagram by Sift on Nov 14, 2023']);
		expect(host?.querySelector<HTMLAnchorElement>('.filters a')?.getAttribute('href')).toBe(
			'/sites/s1'
		);
		restore();
	});

	it('says "a Site" where the Site cannot be named, and still draws the filter', async () => {
		/* A Site this viewer may not be shown answers the same miss as one nobody made; the wall is
		   empty for the same reason, and the cross is the way out. */
		const restore = answeringSites({});
		draw('?filed=s9~~');
		await settle();

		/* `barChips` keeps plain ASCII only, so the dash between is not in what it reads. */
		expect(barChips()).toEqual(['filed under: a Site by hand no date recorded']);
		expect(host?.querySelector('.filters a')).toBeNull();
		restore();
	});

	it('takes one line off on its cross, and leaves the other line and every filter', async () => {
		const restore = answeringSites({ s1: 'Instagram' });
		draw('?filed=s1~sift~2023-11-14&filed=s1~sift~2023-11-15&tags=beach');
		await settle();

		host?.querySelector<HTMLElement>('.filters .chip .remove')?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		const next = new URL(held.gone[0], 'http://x').searchParams;
		expect(next.getAll('filed')).toEqual(['s1~sift~2023-11-15']);
		expect(next.getAll('tags')).toEqual(['beach']);
		restore();
	});

	it('draws a line nobody could read as the address holds it, never dropping it', () => {
		draw('?filed=garbage');

		expect(barChips()).toEqual(['filed under: garbage']);
	});
});

describe('a kept filter saved on a username wall', () => {
	/* `username` is saveable like the named filters. The wall of one username's files can be kept (the row
	   counts the username there), and a kept filter holding a username is not recognised on the same
	   filters WITHOUT it, as if the username were not part of it. */
	const esmewrenfield = { id: 'ac1', username: 'esmewrenfield', site: 'SomeSite', person_id: null };

	function drawOnAUsername(at: string) {
		held.url = new URL(`http://x/browse${at}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, username: esmewrenfield });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	it('can be kept when the username is the only thing narrowing the wall, and keeps it', async () => {
		vi.mocked(api.post).mockClear();
		drawOnAUsername('?username=ac1');

		const add = host?.querySelector<HTMLElement>('[aria-label="Add to saved filters"]');
		expect(add, 'nothing offered to keep a wall narrowed to a username').not.toBeNull();
		add?.click();
		flushSync();
		// The sheet is portalled out of the bar, so it is asked of the document.
		const named = document.querySelector<HTMLInputElement>('form#keep-filter input');
		named!.value = 'Her uploads';
		named!.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		document
			.querySelector('form#keep-filter')
			?.dispatchEvent(new Event('submit', { cancelable: true }));
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();

		const sent = vi.mocked(api.post).mock.calls.find(([path]) => path === '/search/saved')?.[1] as
			{ body: { query: string } } | undefined;
		expect(sent, 'nothing was saved').toBeDefined();
		expect(new URLSearchParams(sent!.body.query).get('username')).toBe('ac1');
	});

	it('lists what it keeps as the bar draws it, and nothing on the list acts', () => {
		/* The sheet shows the filters being named as the chips they are on the row: the same
		   component, so the same colours and the same words, and none of them can be taken off or
		   followed from inside a sheet whose question is only the name. */
		drawOnAUsername('?username=ac1');
		host?.querySelector<HTMLElement>('[aria-label="Add to saved filters"]')?.click();
		flushSync();

		const kept = document.querySelector('form#keep-filter [aria-label="The filters it keeps"]');
		expect(kept, 'the sheet lists nothing it keeps').not.toBeNull();
		const onTheBar = host?.querySelector('.narrows');
		expect(kept!.textContent?.replace(/\s+/g, ' ').trim()).toContain('esmewrenfield');
		expect(onTheBar!.textContent).toContain('esmewrenfield');
		expect(kept!.querySelector('a, button'), 'a chip in the sheet answers a press').toBeNull();
	});

	it('wears the funnel with a plus, not the floppy disk', () => {
		/* Keeping the filtering is drawn with the filtering's own mark plus an add. A floppy disk
		   says "write a file down", and is what a copy saved to the device wears in History,
		   so the same glyph on both would be one picture for two acts. Read from the generated map
		   because that is what `Icon` draws: a name missing from the font renders nothing at all. */
		expect(
			codepoints,
			'the generated icon map predates this glyph: run `npm run fonts` (the build does)'
		).toHaveProperty('filter_plus');
		drawOnAUsername('?username=ac1');
		const glyph = (name: string) =>
			String.fromCodePoint(parseInt((codepoints as Record<string, string>)[name], 16));

		const drawnGlyph = host
			?.querySelector('[aria-label="Add to saved filters"] .icon')
			?.textContent?.trim();
		expect(drawnGlyph).toBe(glyph('filter_plus'));
		expect(drawnGlyph).not.toBe(glyph('save'));
	});

	it('puts the username back on when it is applied, where its chip names it again', async () => {
		savedSearches.items = [
			{ id: 'hers', name: 'Her beach', query: 'username=ac1&tags=beach', kind: 'asset' }
		];
		draw();
		await openPanel();

		const pill = [...(host?.querySelectorAll<HTMLElement>('.kept .name') ?? [])].find((one) =>
			one.textContent?.includes('Her beach')
		);
		pill?.click();
		flushSync();

		expect(held.gone).toHaveLength(1);
		const went = new URL(held.gone[0], 'http://x');
		expect(went.pathname).toBe('/browse');
		expect(went.searchParams.get('username')).toBe('ac1');
		expect(went.searchParams.get('tags')).toBe('beach');
	});

	it('names the wall only when the username matches too', () => {
		savedSearches.items = [
			{ id: 'hers', name: 'Her beach', query: 'username=ac1&tags=beach', kind: 'asset' }
		];
		drawOnAUsername('?username=ac1&tags=beach');
		expect(barChips()[0]).toContain('username: @esmewrenfield on SomeSite');
		expect(barChips().some((one) => one.includes('Her beach'))).toBe(true);
	});

	it('does not name the same filters without the username as that kept filter', () => {
		savedSearches.items = [
			{ id: 'hers', name: 'Her beach', query: 'username=ac1&tags=beach', kind: 'asset' }
		];
		draw('?tags=beach');

		expect(barChips().some((one) => one.includes('Her beach'))).toBe(false);
	});
});

describe('a kept filter goes on the screen it was pressed on', () => {
	/*
	 * A kept filter applies to the screen it is pressed on: sending it to `/browse` from a person's
	 * Files tab would leave the person for the whole library, and from the People wall would write
	 * a people filter onto the file grid.
	 */
	function drawAt(path: string, published: Record<string, unknown>) {
		held.url = new URL(`http://x${path}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, ...published });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	function press(name: string) {
		const pill = [...(host?.querySelectorAll<HTMLElement>('.kept .name') ?? [])].find((one) =>
			one.textContent?.includes(name)
		);
		expect(pill, `no kept filter called ${name} was offered`).toBeDefined();
		pill?.click();
		flushSync();
	}

	it("stays on a person's Files tab, keeping the tab and replacing the filters", async () => {
		drawAt('/people/p1?show=files&tags=beach&from=01XYZ', { query: { people: 'Jane Doe' } });
		await openPanel();

		press('Runway clips');

		expect(held.gone).toHaveLength(1);
		const went = new URL(held.gone[0], 'http://x');
		expect(went.pathname, 'the person was left for the library').toBe('/people/p1');
		expect(went.searchParams.get('show')).toBe('files');
		expect(went.searchParams.getAll('tags')).toEqual(['runway']);
		expect(went.searchParams.get('people')).toBe('Anna');
		// A place in the previous answer is not a place in the new one.
		expect(went.searchParams.has('from')).toBe(false);
	});

	it('keeps a filter about people on the People wall', async () => {
		drawAt('/people?sort=largest', { subject: 'person' });
		await openPanel();

		press('Redheads');

		const went = new URL(held.gone[0], 'http://x');
		expect(went.pathname).toBe('/people');
		expect(went.searchParams.get('hair_color')).toBe('RED');
		expect(went.searchParams.get('sort'), "the screen's own order went with it").toBe('largest');
	});

	it("never switches the tab it was pressed on, and carries the kept filter's own order", async () => {
		savedSearches.items = [
			{
				id: 'looped',
				name: 'Shuffled beach',
				query: 'show=loops&tags=beach&sort=random&seed=7',
				kind: 'asset'
			}
		];
		drawAt('/people/p1?show=files&sort=newest', { query: { people: 'Jane Doe' } });
		await openPanel();

		press('Shuffled beach');

		const went = new URL(held.gone[0], 'http://x');
		expect(went.searchParams.getAll('show'), 'the press switched the tab').toEqual(['files']);
		expect(went.searchParams.get('tags')).toBe('beach');
		expect(went.searchParams.getAll('sort')).toEqual(['random']);
		expect(went.searchParams.get('seed')).toBe('7');
	});
});

/*
 * Pointing at what filters, on a screen that filters something else. Theater hands the bar a
 * filtering with `pointing` on it, and the bar spreads it over the two things that narrow the
 * cells: the chips on the row and the columns in the panel. Not the columns while a kept filter is
 * being edited, because then they filter a draft that is not on screen.
 */
describe('pointing at what narrows', () => {
	function drawOverACell() {
		held.url = new URL('http://x/theater');
		held.gone.length = 0;
		const pointing = {
			onmouseenter: vi.fn(),
			onmouseleave: vi.fn(),
			onfocus: vi.fn(),
			onblur: vi.fn()
		};
		const narrowing = {
			read: () => new URLSearchParams('tags=beach'),
			write: vi.fn(),
			pointing
		};
		screenBar.publish(owner, { filterable: true, narrowing });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
		return pointing;
	}

	it('lights the cells from a chip and from a column', async () => {
		const pointing = drawOverACell();

		host?.querySelector('.narrows')?.dispatchEvent(new MouseEvent('mouseenter'));
		expect(pointing.onmouseenter, 'pointing at a chip lit nothing').toHaveBeenCalledTimes(1);

		await openPanel();
		host?.querySelector('.columns')?.dispatchEvent(new MouseEvent('mouseenter'));
		expect(pointing.onmouseenter, 'pointing at a column lit nothing').toHaveBeenCalledTimes(2);
	});

	it('does not light the cells from a column that is editing a kept filter', async () => {
		const pointing = drawOverACell();
		await openPanel();
		savedSearches.editing = {
			id: 'kept',
			name: 'Runway clips',
			draft: 'tags=runway&people=Anna',
			kind: 'asset'
		};
		flushSync();

		host?.querySelector('.columns')?.dispatchEvent(new MouseEvent('mouseenter'));
		expect(
			pointing.onmouseenter,
			'a draft that is not on screen lit the wall'
		).not.toHaveBeenCalled();
	});
});

describe('a wall of things reads a refusal and the words as the wall of files does', () => {
	function drawOver(path: string, subject: 'asset' | 'person' | 'site', at = '') {
		held.url = new URL(`http://x${path}${at}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, subject });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	/** The query the panel sent with its count of one column. */
	function countedWith(path: string): Record<string, unknown> | undefined {
		const asked = vi
			.mocked(api.get)
			.mock.calls.filter(([where]) => where === path)
			.at(-1);
		return (asked?.[1] as { query?: Record<string, unknown> } | undefined)?.query;
	}

	it('refuses one value from its chip and leaves the other values of that facet', () => {
		drawOverPeople('?hair_color=BLONDE&hair_color=RED');
		/* The chips are drawn in the address's order, so the second is RED. */
		const chip = host?.querySelectorAll<HTMLElement>('.filters .chip')[1];
		const press = chip?.querySelector<HTMLElement>('button.body');
		expect(press, 'the chip on a wall of people offered no refusal').toBeTruthy();
		press?.click();
		flushSync();

		const gone = new URL(held.gone[0] ?? '', 'http://x');
		expect(gone.searchParams.getAll('hair_color')).toEqual(['BLONDE', '-RED']);
	});

	it('refuses a Site from its chip on Downloads, which reads the minus as every wall does', () => {
		held.url = new URL('http://x/downloads?site=YouTube');
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, subject: 'download' });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();

		const press = host?.querySelector<HTMLElement>('.filters .chip button.body');
		expect(press, 'the Downloads chip offered no refusal').toBeTruthy();
		press?.click();
		flushSync();

		const gone = new URL(held.gone[0] ?? '', 'http://x');
		expect(gone.searchParams.getAll('site')).toEqual(['-YouTube']);
	});

	it('refuses one value on a wall of files and keeps the other value of the same name', () => {
		draw('?media=image&media=-video');
		const chip = host?.querySelectorAll<HTMLElement>('.filters .chip')[0];
		chip?.querySelector<HTMLElement>('button.body')?.click();
		flushSync();

		/* Both refused, as one refusal: the panel's own writer keeps a column's refused values
		   together, and "not video or image" is the same set as the two written apart. */
		const gone = new URL(held.gone[0] ?? '', 'http://x');
		expect(gone.searchParams.getAll('media')).toEqual(['-video|image']);
	});

	it('keeps a refused value when another value in its column is picked', async () => {
		drawOver('/people', 'person', '?hair_color=-RED');
		await openPanel();
		const row = [...(host?.querySelectorAll<HTMLElement>('.column .value') ?? [])].find((one) =>
			/blonde/i.test(one.textContent ?? '')
		);
		expect(row, 'the hair colour column drew nothing to pick').toBeTruthy();
		row?.click();
		flushSync();

		const gone = new URL(held.gone[0] ?? '', 'http://x');
		expect(gone.searchParams.getAll('hair_color').sort()).toEqual(['-RED', 'BLONDE']);
	});

	it('counts a wall of things under its words, as a name matched anywhere', async () => {
		drawOver('/sites', 'site', '?q=gram');
		await openPanel();

		const query = countedWith('/sites/facets');
		expect(query, 'no column was counted at all, so this proves nothing').toBeTruthy();
		expect(query?.prefix).toBe('gram');
		expect(query?.anywhere).toBe('true');
		expect(query?.q, 'the words went as a parameter the route drops').toBeUndefined();
	});

	it('hands the words to the file route as they are', async () => {
		drawOver('/browse', 'asset', '?q=beach');
		await openPanel();

		const query = countedWith('/assets/facets');
		expect(query?.q).toBe('beach');
		expect(query?.prefix).toBeUndefined();
	});
});

describe('a chip naming a thing by its id', () => {
	const NETWORK = '01ARZ3NDEKTSV4RRFFQ69G5FAV';

	it('draws the network by name, asked of its own page, rather than drawing the id', async () => {
		held.url = new URL(`http://x/sites?parent=${NETWORK}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, subject: 'site' });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();

		expect(vi.mocked(api.get).mock.calls.some(([path]) => path === `/sites/${NETWORK}`)).toBe(true);
		expect(barChips()).toEqual(['Network: Harbour Network']);
	});
});

describe('a chip on a wall of files naming a thing by its id', () => {
	async function drawnWith(at: string, answer: (path: string) => unknown) {
		const usual = vi.mocked(api.get).getMockImplementation()!;
		vi.mocked(api.get).mockImplementation(async (path, options) => {
			const said = answer(path);
			return said === undefined ? usual(path, options) : (said as never);
		});
		try {
			draw(at);
			for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
			flushSync();
			return barChips().join(' ');
		} finally {
			vi.mocked(api.get).mockImplementation(usual);
		}
	}

	it.each([
		['people', '/people/', '01ARZ3NDEKTSV4RRFFQ69G5FC0'],
		['tags', '/tags/', '01ARZ3NDEKTSV4RRFFQ69G5FC1'],
		['sites', '/sites/', '01ARZ3NDEKTSV4RRFFQ69G5FC2'],
		['collections', '/collections/', '01ARZ3NDEKTSV4RRFFQ69G5FC3'],
		['photo_sets', '/photo-sets/', '01ARZ3NDEKTSV4RRFFQ69G5FC4'],
		['songs', '/songs/', '01ARZ3NDEKTSV4RRFFQ69G5FC5']
	])('draws %s by the name its own read gives', async (facet, read, id) => {
		const chips = await drawnWith(`?${facet}=${id}`, (path) =>
			path === `${read}${id}` ? { name: 'Esme Wrenfield' } : undefined
		);
		expect(chips).toContain('Esme Wrenfield');
		expect(chips).not.toContain(id);
	});

	it('says a thing whose read answers 404 no longer exists', async () => {
		const GONE = '01ARZ3NDEKTSV4RRFFQ69G5FC6';
		const chips = await drawnWith(`?people=${GONE}`, (path) => {
			if (path === `/people/${GONE}`) throw Object.assign(new Error('gone'), { status: 404 });
			return undefined;
		});
		expect(chips).toContain('a person who no longer exists');
		expect(chips).not.toContain(GONE);
	});
});

describe('a chip naming one file by its id', () => {
	/* `like` and `same_music` take a file's ID. A chip arrived at by the file menu or a pasted link
	   asks the file's own read for its name; one this viewer may not have stays the ID. */
	const SEEN = '01ARZ3NDEKTSV4RRFFQ69G5FB0';
	const UNSEEN = '01ARZ3NDEKTSV4RRFFQ69G5FB1';

	async function drawnWith(at: string) {
		const usual = vi.mocked(api.get).getMockImplementation()!;
		vi.mocked(api.get).mockImplementation(async (path, options) => {
			if (path === `/assets/${SEEN}`)
				return { title: null, filename: 'Golden hour.mp4', original_filename: null } as never;
			if (path === `/assets/${UNSEEN}`) throw new Error('not found');
			return usual(path, options);
		});
		try {
			draw(at);
			for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
			flushSync();
			return barChips();
		} finally {
			vi.mocked(api.get).mockImplementation(usual);
		}
	}

	it('draws a similar-files filter as the file it is about', async () => {
		const chips = await drawnWith(`?like=${SEEN}`);
		expect(chips.join(' ')).toContain('Golden hour.mp4');
		expect(chips.join(' ')).not.toContain(SEEN);
	});

	it('names a same-music filter the same way', async () => {
		const chips = await drawnWith(`?same_music=${SEEN}`);
		expect(chips.join(' ')).toContain('Golden hour.mp4');
	});

	it('goes on drawing the id for a file this viewer may not have', async () => {
		const chips = await drawnWith(`?like=${UNSEEN}`);
		expect(chips.join(' ')).toContain(UNSEEN);
	});
});

describe('a chip naming one person by their id', () => {
	/* `unnamed_face` takes a person's ID: the line under the faces on their page writes it. The
	   chip asks the person's own read for the name, so it never reads as an ID. */
	const PERSON = '01ARZ3NDEKTSV4RRFFQ69G5FC0';

	it('draws the folder files with a face still unnamed as the person they are filed under', async () => {
		const usual = vi.mocked(api.get).getMockImplementation()!;
		vi.mocked(api.get).mockImplementation(async (path, options) => {
			if (path === `/people/${PERSON}`) return { name: 'Marisol Vane' } as never;
			return usual(path, options);
		});
		try {
			draw(`?unnamed_face=${PERSON}`);
			for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
			flushSync();
			const chips = barChips().join(' ');
			expect(chips).toContain('Marisol Vane');
			expect(chips).not.toContain(PERSON);
		} finally {
			vi.mocked(api.get).mockImplementation(usual);
		}
	});
});

describe('the words a wall is searched by', () => {
	function drawOnLoops(at: string) {
		held.url = new URL(`http://x/loops${at}`);
		held.gone.length = 0;
		screenBar.publish(owner, { filterable: true, words: 'called' });
		host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
		flushSync();
	}

	it('draws the words a wall keeps under its own name as the words chip', () => {
		drawOnLoops('?called=dusk');
		expect(barChips()).toContain('words: dusk');
	});

	it('takes them off with the chip cross, and leaves the rest of the address alone', () => {
		drawOnLoops('?called=dusk&tags=beach');
		host?.querySelector<HTMLElement>('[aria-label="Remove the words dusk"]')?.click();
		flushSync();

		const went = new URL(held.gone.at(-1) ?? '', 'http://x');
		expect(went.searchParams.get('called')).toBeNull();
		expect(went.searchParams.get('tags')).toBe('beach');
	});

	it('draws no second chip for `q` words where the wall names none of its own', () => {
		draw('?called=dusk');
		expect(barChips().some((chip) => chip.startsWith('words:'))).toBe(false);
	});

	it('takes the typed words off `q` with their cross, keeping every clause', async () => {
		vi.mocked(api.get).mockImplementationOnce(async () => ({
			text: 'dusk',
			clauses: [{ field: 'tags', values: ['beach'], query: 'tags:beach' }],
			terms: {},
			problems: []
		}));
		draw('?q=tags%3Abeach+dusk');
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		host?.querySelector<HTMLElement>('[aria-label="Remove the words dusk"]')?.click();
		flushSync();

		const went = new URL(held.gone.at(-1) ?? '', 'http://x');
		expect(went.searchParams.get('q')).toBe('tags:beach');
	});
});
