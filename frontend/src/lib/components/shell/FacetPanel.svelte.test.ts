/*
 * The one dimension in the chooser that is not a dimension: a date span.
 *
 * Every other column is a list of values with counts, grouped by the server. A date is neither:
 * `added:` takes a range, and the panel draws a calendar and reads the answer out of the query it
 * was handed. The span-ness is declared once, and the fetch must read it too, or choosing the
 * column would ask the server to group files by a date, be refused with a 422 on every change, and
 * show nothing wrong, because the calendar never reads the answer.
 *
 * Driven through the component rather than a helper, because the fetch is what matters: a test of
 * the drawing alone would pass either way.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const asked: string[] = [];
/** Which ROUTE each column was counted by. A facet belongs to a noun, and so does the route. */
const routes: string[] = [];

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			asked.push(String(options?.query?.facet ?? path));
			routes.push(path);
			/*
			 * A real value with a distinctive count: a panel that draws nothing would satisfy "it
			 * did not say Counting..." without being right.
			 */
			return {
				facet: String(options?.query?.facet ?? ''),
				values: [{ value: 'video', count: 4242 }]
			};
		})
	},
	ApiError: class extends Error {}
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

const FacetPanel = (await import('./FacetPanel.svelte')).default;
const { facetCounts } = await import('./facet-counts.svelte');

let host: HTMLElement | undefined;
let drawn: Record<string, unknown> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	host?.remove();
	drawn = undefined;
	host = undefined;
	asked.length = 0;
	routes.length = 0;
	/* The panel REMEMBERS which columns it is showing now, and jsdom's storage outlives a test. So
	   one test that changed them would decide what the next one opened on, and a passing check
	   could be asserting the columns another test had left behind. */
	localStorage.clear();
	/* The counts are held for the tab, so one test's answers would be the next one's. */
	facetCounts.reset();
});

/** The panel, on a screen asking nothing in particular. */
function draw(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FacetPanel, {
		target: host,
		props: {
			query: {},
			onpick: () => {},
			onset: () => {},
			stance: () => 'off' as const,
			chosen: () => [],
			...extra
		}
	}) as Record<string, unknown>;
}

/** The component's own source, for the one assertion a layout-less environment cannot make. */
const WHOLE_FILE = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), 'FacetPanel.svelte'),
	'utf8'
);

/*
 * Comments stripped, so a note naming an expression can never satisfy (or fail) a check for that
 * expression in the code.
 */
const SOURCE = WHOLE_FILE.replace(/\/\*[\s\S]*?\*\//g, ' ')
	.replace(/<!--[\s\S]*?-->/g, ' ')
	.replace(/\/\/[^\n]*/g, ' ');

/** Where the dimensions are declared. See the span test below for why it is read as text. */
const DIMENSIONS = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), 'facet-labels.ts'),
	'utf8'
);

describe('what the panel asks the server for', () => {
	it("counts every column within the target's own narrowing, that column's own included", async () => {
		/* A theater cell that plays video and GIF offers no photographs in its Media column: its
		   kinds are not a pick the panel can take back, so `within` holds for every column, that
		   one's own included, beside a pick of the same name. */
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockClear();
		draw({
			lead: ['media', 'people'],
			query: { media: 'video', people: 'someone' },
			within: { media: 'video|gif' }
		});
		await Promise.resolve();
		await Promise.resolve();
		const sent = Object.fromEntries(
			vi.mocked(api.get).mock.calls.map(([, options]) => {
				const query = (options as { query: Record<string, unknown> }).query;
				return [String(query.facet), query];
			})
		);

		expect(sent.media?.media, 'the Media column counts within the cell').toBe('video|gif');
		expect(sent.people?.media, 'and every other column too, beside the pick').toEqual([
			'video',
			'video|gif'
		]);
	});

	it('asks about the columns it opens with', async () => {
		draw();
		await Promise.resolve();
		await Promise.resolve();

		// The positive control. Without it a panel that asked for NOTHING would satisfy every
		// assertion below, and asking for nothing is one of the ways this can go wrong.
		expect(asked.length, 'the panel asked the server for nothing at all').toBeGreaterThan(0);
	});

	it('never asks it to group files by a date, once that column is on screen', async () => {
		/*
		 * Paged to, and that is the whole test. The panel opens on the first five dimensions and
		 * the date is near the end of the list, so a panel left alone never asks about it whatever
		 * the code does; the column must be brought on screen before "it was not asked about" means
		 * anything.
		 *
		 * Pressed until the column is actually drawn, not a fixed number of times: a press count
		 * keyed on the length of a list people add to stops reaching the column as the list grows.
		 * The drawing is what the control asserts.
		 */
		draw();
		const further = host!.querySelector('[aria-label="Further filters"]') as HTMLElement;
		expect(further, 'the panel draws no way to reach the later columns').not.toBeNull();

		/* The span column is the one that draws a calendar instead of a list to tick, and `.when` is
		   the panel's own wrapper around it, so this asks what the panel DREW rather than what it
		   was told to show. Bounded well past the length of the list, because a pager that has stopped
		   moving must end the test rather than hang it. */
		const dateColumnShowing = () => host!.querySelector('.when') !== null;
		for (let press = 0; press < 40 && !dateColumnShowing(); press += 1) {
			further.click();
			flushSync();
		}
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));

		// The column really is showing now, or the assertion after it is over an empty set again.
		expect(
			dateColumnShowing(),
			'the date column was never paged to, so the check below is empty'
		).toBe(true);
		/* Paged past at speed, the pages between are never asked (one batch out, the newest next). */
		const firstFive = ['media', 'tags', 'people', 'in', 'rating'];
		expect(
			asked.some((key) => !firstFive.includes(key)),
			'the later columns were never reached'
		).toBe(true);
		expect(asked, 'the server has no such dimension and answers 422').not.toContain('added');
	});

	it('and it still asks about real dimensions, so the refusal is specific', async () => {
		// The other half of the assertion above. A panel that asked for nothing, or that had lost the
		// column entirely, would also never ask about a date.
		draw();
		await Promise.resolve();
		await Promise.resolve();

		expect(asked).toContain('media');
		expect(asked).toContain('tags');
	});

	it('the column is still offered, and says once that it is a span', () => {
		/*
		 * Read from the file, and only this one is. The chooser is a headless-library dropdown that
		 * positions its list against its trigger, which needs a layout this environment lacks, so
		 * the column cannot be switched to here and whether the calendar draws is answered in the
		 * browser suite. What is answered here is that the entry exists and that span-ness is
		 * declared once, in the list the fetch reads.
		 */
		expect(DIMENSIONS).toContain("key: 'added'");
		expect(
			DIMENSIONS.match(/span: true/g),
			'the span-ness is declared somewhere other than once, in the list'
		).toHaveLength(1);
		expect(
			SOURCE.match(/facet === 'added'/g),
			'the markup is deciding again, separately, which column is a span'
		).toBeNull();
	});
});

describe('what somebody sees when the panel opens again', () => {
	it('draws the numbers it already had, with no round trip to wait for', async () => {
		/*
		 * Pointing at Filter must not flash "Counting..." on every hover. The panel is unmounted
		 * when the menu shuts (a hidden copy would sit in the tab order), so without the cache each
		 * reopen would re-fetch counts it had just thrown away.
		 *
		 * It asserts the presence of the values drawn, not the absence of "Counting...": that flag
		 * is raised inside the async body a frame later, so its absence proves nothing. With no
		 * promise awaited between mounting and reading, a cached answer (applied in the effect
		 * body) is on screen at `flushSync`, and a fetched one cannot be.
		 */
		draw();
		flushSync();
		await new Promise((settle) => setTimeout(settle, 0));
		expect(host!.textContent, 'the first open never got its counts').toContain('video');

		unmount(drawn!);
		host!.remove();
		drawn = undefined;

		draw();
		flushSync();

		expect(host!.textContent, 're-opening the panel threw away counts it already had').toContain(
			'video'
		);
	});

	it('cannot show counts taken while the vault was open once it has been shut', async () => {
		/*
		 * The half of the cache key that is not an optimisation. Counts taken while hidden items were
		 * showing include them, so a key that ignored the vault would draw those numbers for a moment
		 * after somebody locked it, which is the one thing locking it is for.
		 */
		const STORE = readFileSync(
			join(dirname(fileURLToPath(import.meta.url)), 'facet-counts.svelte.ts'),
			'utf8'
		);
		expect(STORE).toMatch(/function keyOf[\s\S]*?vault\.generation/);
	});

	it('opens on counts the bar asked for before it opened, with no "Counting..." frame', async () => {
		facetCounts.ask({
			noun: 'asset',
			facets: ['media', 'tags', 'people', 'in', 'rating'],
			query: {},
			within: {}
		});
		await new Promise((settle) => setTimeout(settle, 0));
		asked.length = 0;
		draw();
		flushSync();
		expect(host!.textContent).toContain('video');
		expect(host!.textContent).not.toContain('Counting');
		expect(asked, 'the panel asked again for counts it was handed').toEqual([]);
	});
});

/*
 * The columns somebody chose, kept.
 *
 * The panel is unmounted whenever the menu shuts, so without a note every column swapped in would
 * be thrown away the moment it closed: it would open on the same first five every single time, with
 * nothing on screen to suggest the choice had not been kept.
 *
 * Driven through the STORED NOTE rather than through the chooser, because picking a dimension means
 * opening a `bits-ui` Select, which cannot be driven in jsdom at all: opening it needs a synthetic
 * keydown and choosing an item does not fire `onValueChange` through a click. What is provable here
 * is that the panel reads a note, refuses a bad one, and writes what it ends up showing.
 */
describe('the columns somebody chose', () => {
	/* PER NOUN. One note for every wall would be wrong on whichever wall was not the last one
	   visited (a person's columns and a file's share not one key) and the validation would then
	   drop every entry and quietly write the app's own five down as the person's choice. */
	const KEY = 'sift.filters.columns.asset';

	it('opens on what was remembered rather than on the first five', async () => {
		localStorage.setItem(KEY, 'vcodec,acodec,rating,viewed,duration');
		draw();
		await Promise.resolve();
		await Promise.resolve();

		// The panel asks the server about exactly the columns it is showing, so what it ASKED is
		// what it opened on, which is readable here where the column headings are not.
		expect(asked).toContain('vcodec');
		expect(asked).toContain('acodec');
		expect(asked).not.toContain('media');
	});

	it('writes down what it ends up showing', async () => {
		draw();
		await Promise.resolve();

		expect(localStorage.getItem(KEY)).toBe('media,tags,people,in,rating');
	});

	it('drops a dimension this version no longer has, and fills the gap', async () => {
		/* A note written by an older build. Silently trusting it would leave the panel with four
		   columns and a hole, or asking the server about a dimension it refuses. */
		localStorage.setItem(KEY, 'vcodec,knitting,acodec');
		draw();
		await Promise.resolve();
		await Promise.resolve();

		expect(asked).not.toContain('knitting');
		expect(asked).toHaveLength(5);
	});

	it('drops a duplicate rather than spending two columns saying one thing', async () => {
		localStorage.setItem(KEY, 'vcodec,vcodec,vcodec');
		draw();
		await Promise.resolve();
		await Promise.resolve();

		expect(asked.filter((one) => one === 'vcodec')).toHaveLength(1);
		expect(asked).toHaveLength(5);
	});
});

describe('a column that admits it is hiding values', () => {
	/*
	 * The box and the "View n more" are two halves of one question and answer to the same number,
	 * the cut, so no column admits it is hiding values while offering only a control that unrolls
	 * them all.
	 *
	 * The api mock above answers every column with one value, so the count is set per test by
	 * replacing what it returns.
	 */
	function answering(howMany: number) {
		const values = Array.from({ length: howMany }, (_, at) => ({ value: `v${at}`, count: at }));
		return async (path: string, options?: { query?: Record<string, unknown> }) => {
			asked.push(String(options?.query?.facet ?? path));
			routes.push(path);
			return { facet: String(options?.query?.facet ?? ''), values };
		};
	}

	async function boxesAfter(howMany: number) {
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockImplementation(answering(howMany) as never);
		draw();
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		return {
			boxes: host?.querySelectorAll('input.narrow').length ?? 0,
			more: host?.querySelectorAll('.column .more').length ?? 0
		};
	}

	it('offers no box while every value it has is already on screen', async () => {
		// Five is the cut, so five is all of them: there is nothing hidden and nothing to filter to.
		expect(await boxesAfter(5)).toEqual({ boxes: 0, more: 0 });
	});

	it('offers a box the moment it starts hiding one', async () => {
		/*
		 * Six, not thirteen: a column with more rows than it shows must offer a box, not only "View
		 * 1 more".
		 */
		const shown = await boxesAfter(6);
		expect(shown.more).toBeGreaterThan(0);
		expect(shown.boxes).toBe(shown.more);
	});
});

describe('a column whose values are a LADDER', () => {
	/*
	 * Two columns are bands (Duration and File size), because no two files share a duration or a
	 * size, so a column over the raw value would be a column of ones.
	 *
	 * Rows come back ordered by file count, right for a list of tags and wrong for a ladder: "15 to
	 * 30 minutes" above "Under 1 minute" has to be read twice.
	 *
	 * The order is all being a ladder changes: a banded column still has the cut, the fold and the
	 * box like the four beside it, and with the rows in ladder order the fold takes the tail, in a
	 * known place.
	 */
	const SHUFFLED = [
		{ value: '5m..<15m', count: 900 },
		{ value: '60m+', count: 12 },
		{ value: '0s..<1m', count: 400 },
		{ value: '30m..<60m', count: 80 },
		{ value: '1m..<3m', count: 700 },
		{ value: '3m..<5m', count: 500 },
		{ value: '15m..<30m', count: 300 }
	];

	async function drawDurationAnswering(values: { value: string; count: number }[]) {
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockImplementation((async (
			path: string,
			options?: { query?: Record<string, unknown> }
		) => {
			const facet = String(options?.query?.facet ?? '');
			asked.push(facet || path);
			routes.push(path);
			return { facet, values: facet === 'duration' ? values : [] };
		}) as never);
		draw({ lead: ['duration'] });
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
	}

	/** What the first column's rows say, in the order they are drawn. */
	function rows(): string[] {
		const column = host?.querySelector('.column');
		return [...(column?.querySelectorAll('.value') ?? [])].map((one) =>
			(one.textContent ?? '').replace(/[^\x20-\x7E]/g, '').trim()
		);
	}

	/** Press the column's own "View n more" and settle. */
	function unfold() {
		const more = host?.querySelector('.column .more') as HTMLElement | null;
		more?.click();
		flushSync();
	}

	/** Type into the column's own filter box and settle. */
	function narrowTo(text: string) {
		const box = host?.querySelector('input.narrow') as HTMLInputElement | null;
		if (!box) throw new Error('the ladder offered no box to type in');
		box.value = text;
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
	}

	it('draws its bands in the order they were cut, whatever order they arrive in', async () => {
		await drawDurationAnswering(SHUFFLED);
		unfold();

		expect(rows().map((one) => one.replace(/[\d,]+$/, '').trim())).toEqual([
			'Under 1 minute',
			'1 to 3 minutes',
			'3 to 5 minutes',
			'5 to 15 minutes',
			'15 to 30 minutes',
			'30 to 60 minutes',
			'60 minutes or more'
		]);
	});

	it('cuts at five and folds the tail, exactly like every other column', async () => {
		/*
		 * This column has the type-to-filter box and the see-more fold like every other. What folds
		 * is the end of the ladder, because the rows are in ladder order before the cut is taken.
		 */
		await drawDurationAnswering(SHUFFLED);

		expect(rows()).toHaveLength(5);
		expect(rows()[4]).toContain('15 to 30 minutes');
		const more = host?.querySelector('.column .more');
		expect(more?.textContent).toContain('View 2 more');
		expect(host?.querySelectorAll('input.narrow').length, 'a ladder offered no box').toBe(1);

		unfold();
		expect(rows()).toHaveLength(7);
	});

	it('narrows by the words on screen, not only by the stored band', async () => {
		/* A band is stored `1m..<3m` and read "1 to 3 minutes". Matching the stored form alone made
		   the box useless on the one column it was just given to: everything a person can see to
		   type matches nothing. Both forms match, so knowing the filter's grammar still works. */
		await drawDurationAnswering(SHUFFLED);

		narrowTo('minutes');
		/* Five, and "Under 1 minute" is not among them: it says minute, singular. Still in ladder
		   order, and still cut at five like any other column. */
		expect(rows().map((one) => one.replace(/[\d,]+$/, '').trim())).toEqual([
			'1 to 3 minutes',
			'3 to 5 minutes',
			'5 to 15 minutes',
			'15 to 30 minutes',
			'30 to 60 minutes'
		]);

		narrowTo('60m+');
		expect(rows().map((one) => one.replace(/[\d,]+$/, '').trim())).toEqual(['60 minutes or more']);
	});

	it('keeps a band it has never heard of, at the end', async () => {
		/* A client one release behind the server would otherwise DROP a row: a band this table does
		   not know is still a real row of a real library, and the person can still click it. */
		await drawDurationAnswering([{ value: '2h+', count: 3 }, ...SHUFFLED]);
		unfold();

		expect(rows()).toHaveLength(8);
		expect(rows()[7]).toContain('2h+');
	});
});

describe('the columns an EDIT opens on', () => {
	/*
	 * Editing a kept filter takes over the screen, and the panel opens on whatever five columns were
	 * last chosen. A filter about people, ratings and durations could therefore open on five columns
	 * it touches none of, which reads as a filter holding nothing at all.
	 *
	 * So the caller hands in the dimensions the filter NAMES and they come first. What is worth
	 * pinning is that they lead, that everything else keeps its own order behind them, and the two
	 * things easy to break here: the pager still works, and the person's own arrangement is not
	 * overwritten by the app's.
	 */
	/* The trigger's text carries the chevron's own ligature, which is not a space and does not trim.
	   Anything outside plain ASCII is dropped before comparing. */
	function headings(): string[] {
		return [...(host?.querySelectorAll('.column > button') ?? [])].map((one) =>
			(one.textContent ?? '').replace(/[^\x20-\x7E]/g, '').trim()
		);
	}

	it('leads with the dimensions it was handed', async () => {
		draw({ lead: ['rating', 'duration'] });
		flushSync();

		expect(headings().slice(0, 2)).toEqual(['Rating', 'Duration']);
	});

	it('keeps everything else in its ordinary order behind them', async () => {
		/*
		 * A sort by "is it in the lead" would put them first and say nothing about the rest. Paging
		 * past the filter's own columns has to land on the ordinary next five, not a reshuffle.
		 */
		draw({ lead: ['rating'] });
		flushSync();

		expect(headings()).toEqual(['Rating', 'Media', 'Tags', 'People', 'Folder']);
	});

	it('still pages', async () => {
		/* A prop with a DEFAULT is a fresh value on every read, so an effect reading `lead` directly
		   would re-run whenever any prop changed, and this one resets the page. Pressing forward
		   would move the columns and they would be put straight back. */
		draw({ lead: ['rating'] });
		// NO `flushSync` before the press, deliberately. Effects run after the component mounts, so the
		// real ordering is: drawn, pressed, effect. Flushed first, the seeding has already happened and
		// the test cannot see an effect that undoes a press, which is exactly the fault.
		const further = host!.querySelector('[aria-label="Further filters"]') as HTMLElement;
		further.click();
		flushSync();

		expect(headings()[0]).not.toBe('Rating');
	});

	it("does not write the app's own choice down as the person's", async () => {
		/* The remembered columns are a preference somebody set. An edit borrows the panel for a moment
		   and must give it back: recorded, the arrangement would be lost to a filter they opened
		   once. */
		localStorage.setItem('sift.filters.columns.asset', 'vcodec,acodec,media,tags,people');
		draw({ lead: ['rating', 'duration'] });
		flushSync();

		expect(localStorage.getItem('sift.filters.columns.asset')).toBe(
			'vcodec,acodec,media,tags,people'
		);
	});

	it('opens on the remembered columns again once the lead is gone', async () => {
		localStorage.setItem('sift.filters.columns.asset', 'vcodec,acodec,media,tags,people');
		draw();
		flushSync();

		expect(headings()[0]).toBe('Video codec');
	});
});

/*
 * The panel's shape is a property of the noun. A wall of people offers the person columns wherever
 * it is drawn (the People page, a site's People tab, a tag's People tab), and a wall of files
 * offers the file columns.
 *
 * Driven through what the panel asks the server rather than the headings, for the reason the column
 * tests give above: a heading is a `bits-ui` trigger and this environment has no layout. What it
 * asked is the same fact where jsdom can read it.
 */
describe('the columns belong to the noun on the wall', () => {
	it('opens on the PERSON columns, in the order that noun declares', async () => {
		draw({ subject: 'person' });
		await Promise.resolve();
		await Promise.resolve();

		expect(asked).toEqual(['gender', 'hair_color', 'eye_color', 'ethnicity', 'country']);
	});

	it('opens on the FILE columns when no noun is named, which is what six screens are', async () => {
		draw();
		await Promise.resolve();
		await Promise.resolve();

		expect(asked).toEqual(['media', 'tags', 'people', 'in', 'rating']);
	});

	it('counts through that noun s own route, never the file one', async () => {
		draw({ subject: 'site' });
		await Promise.resolve();
		await Promise.resolve();

		expect(new Set(routes)).toEqual(new Set(['/sites/facets']));
		// `kind` is not a Sites wall column, on either side: almost no site record carries one, so
		// it would be a single row reading "Site" over the whole wall.
		expect(asked).toEqual(['parent', 'tags', 'enriched', 'usernames', 'cover']);
	});

	it('remembers a choice of columns PER NOUN', async () => {
		/* One note for every wall is a note that is wrong on whichever wall was not the last one
		   visited: not one key is shared between the two lists, so every entry would be dropped as
		   unknown and the panel would open on the app's own five while claiming to remember. */
		localStorage.setItem('sift.filters.columns.asset', 'vcodec,acodec,media,tags,people');
		localStorage.setItem('sift.filters.columns.person', 'age,tags,enriched,cover,gender');
		draw({ subject: 'person' });
		await Promise.resolve();
		await Promise.resolve();

		expect(asked).toEqual(['age', 'tags', 'enriched', 'cover', 'gender']);
	});

	it('moves the old note, which was the file wall s, rather than dropping it', async () => {
		/* The file wall's note under the older key, `sift.filters.columns`, has no noun on it. Left
		   where it is, it is a key nothing reads: somebody's arrangement, quietly reset. */
		localStorage.setItem('sift.filters.columns', 'vcodec,acodec,rating,viewed,duration');
		draw();
		await Promise.resolve();
		await Promise.resolve();

		expect(localStorage.getItem('sift.filters.columns.asset')).toBe(
			'vcodec,acodec,rating,viewed,duration'
		);
		expect(localStorage.getItem('sift.filters.columns'), 'the old key was left behind').toBeNull();
		expect(asked).toContain('vcodec');
	});
});

describe('what a column draws', () => {
	/** Answer every column with exactly these values. */
	async function answering(values: unknown[]) {
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockImplementation((async (
			path: string,
			options?: { query?: Record<string, unknown> }
		) => {
			asked.push(String(options?.query?.facet ?? path));
			routes.push(path);
			return { facet: String(options?.query?.facet ?? ''), values };
		}) as never);
	}

	it('reads the COUNT the server sends, which is of the noun and not of files', async () => {
		/* The field is `count`, not `files`: a wall of people counts people. A panel reading `files`
		   would draw an empty space where every number should be. */
		await answering([{ value: 'video', count: 4242 }]);
		draw();
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();

		expect(host!.textContent).toContain((4242).toLocaleString());
	});

	it('groups a count the way every count on screen is written', async () => {
		/* "25000" beside a wall header reading "8,000 files" would be a raw number where every other
		   count goes through the one formatter (`counted`, `$lib/entity/entity-counts`). */
		await answering([{ value: 'image', count: 25000 }]);
		draw();
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();

		expect(host!.querySelector('.files')?.textContent).toBe('25,000');
	});

	it('names a kind of file in the Media column with the noun the screen uses', async () => {
		/* The value is the query word (`gif`) and the row reads as the screen says it. */
		await answering([{ value: 'gif', count: 3 }]);
		draw();
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();

		const names = [...host!.querySelectorAll('.name')].map((one) => one.textContent?.trim());
		expect(names).toContain('GIFs');
	});

	it('reads the NAME the server sent beside an id, rather than drawing the id', async () => {
		/* A tag facet on a wall of people counts by tag ID, because two tags may share a name. The
		   route sends the name in `label` so that nothing here has to go and look it up. */
		await answering([{ value: '01J5T6R7S8N9P0QAZ2WSX3EDC4', count: 12, label: 'Beach' }]);
		draw({ subject: 'person' });
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();

		expect(host!.textContent).toContain('Beach');
		expect(host!.textContent, 'the raw id reached the screen').not.toContain('01J5T6R7S8');
	});

	it('keeps an empty column, with its heading and a sentence saying why', async () => {
		/* Never hidden. A panel that dropped its empty columns would have a different shape on every
		   page, so the column somebody filters by every day would move under their hand. */
		await answering([]);
		draw({ subject: 'tag' });
		await Promise.resolve();
		await Promise.resolve();
		await new Promise((settle) => setTimeout(settle, 0));
		flushSync();

		expect(host!.querySelectorAll('.column').length, 'the empty columns were dropped').toBe(5);
		expect(host!.textContent).toContain('Nothing to filter by here');
	});
});

/*
 * Pointing at the columns says what they will filter. On Theater the panel edits whichever cells
 * the wall is addressing; the panel spreads whatever the screen hands it over the whole block of
 * columns, gaps included, so moving between columns does not blink the wash off. The handlers are
 * Theater's and tested there.
 */
describe('pointing at the columns', () => {
	it('tells the screen when the pointer arrives and when it leaves', () => {
		const pointing = {
			onmouseenter: vi.fn(),
			onmouseleave: vi.fn(),
			onfocus: vi.fn(),
			onblur: vi.fn()
		};
		draw({ pointing });

		const columns = host?.querySelector('.columns');
		expect(columns, 'no block of columns was drawn').toBeTruthy();
		columns?.dispatchEvent(new MouseEvent('mouseenter'));
		expect(pointing.onmouseenter, 'pointing at a column lit nothing').toHaveBeenCalledTimes(1);
		columns?.dispatchEvent(new MouseEvent('mouseleave'));
		expect(pointing.onmouseleave).toHaveBeenCalledTimes(1);
	});

	it('draws the same columns on a screen that hands it nothing', () => {
		draw();
		expect(host?.querySelector('.columns')).toBeTruthy();
	});
});

describe('a column of numbers, and a column the wall is made of', () => {
	/* The text of each drawn row of the first column, without its glyph ligatures and its count. */
	function firstColumn(): string[] {
		const column = host?.querySelector('.column');
		return [...(column?.querySelectorAll('.value') ?? [])].map((one) =>
			(one.textContent ?? '')
				.replace(/[^\x20-\x7E]/g, '')
				.trim()
				.replace(/\s*[\d,]+$/, '')
		);
	}

	function headingsNow(): string[] {
		return [...(host?.querySelectorAll('.column > button') ?? [])].map((one) =>
			(one.textContent ?? '').replace(/[^\x20-\x7E]/g, '').trim()
		);
	}

	it('lists the ages youngest first, whatever order the counts arrive in', async () => {
		/* One row per year, and a ladder reads upwards: "31" above "24" because more files are under
		   it is a column somebody has to read twice. */
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockImplementation((async (
			_path: string,
			options?: { query?: Record<string, unknown> }
		) => {
			const facet = String(options?.query?.facet ?? '');
			const values =
				facet === 'age'
					? [
							{ value: '31', count: 9 },
							{ value: '24', count: 40 },
							{ value: '27', count: 3 }
						]
					: [];
			return { facet, values };
		}) as never);
		draw({ lead: ['age'] });
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(firstColumn()).toEqual(['24', '27', '31']);
	});

	it('leaves out a facet every row on the wall has one value of, and only that one', () => {
		/* The Loops wall is the files with a Loop: a Loops column there is one row saying so. */
		draw({ lead: ['fav', 'loops'], fixed: ['loops'] });
		flushSync();

		expect(headingsNow()).toContain('Favorites');
		expect(headingsNow()).not.toContain('Loops');
	});
});

describe('a named-thing column of files', () => {
	it('leads with Has and No, outside the five it shows and the box', async () => {
		const { api } = await import('$lib/api/client');
		const values = [
			{ value: 'any', count: 2 },
			{ value: 'none', count: 9 },
			...Array.from({ length: 6 }, (_, at) => ({ value: `t${at}`, count: 1 }))
		];
		vi.mocked(api.get).mockImplementation((async (
			_path: string,
			options?: { query?: Record<string, unknown> }
		) => {
			const facet = String(options?.query?.facet ?? '');
			return { facet, values: facet === 'tags' ? values : [] };
		}) as never);
		draw({ lead: ['tags'] });
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		const column = host?.querySelector('.column');
		const said = [...(column?.querySelectorAll('.value .name') ?? [])].map(
			(one) => one.textContent
		);
		expect(said).toEqual(['Has tags', 'No tags', 't0', 't1', 't2', 't3', 't4']);
		expect(column?.querySelector('.more')?.textContent).toContain('View 1 more');
	});
});

describe('the Tags column of a wall of things', () => {
	async function column(
		subject: 'person' | 'site' | 'collection' | 'photo_set',
		facet: string,
		values: unknown[]
	) {
		const { api } = await import('$lib/api/client');
		vi.mocked(api.get).mockImplementation((async (
			_path: string,
			options?: { query?: Record<string, unknown> }
		) => {
			const asked = String(options?.query?.facet ?? '');
			return { facet: asked, values: asked === facet ? values : [] };
		}) as never);
		draw({ subject, lead: [facet] });
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		const first = host?.querySelector('.column');
		return [...(first?.querySelectorAll('.value .name') ?? [])].map((one) => one.textContent);
	}

	it.each(['person', 'site', 'collection', 'photo_set'] as const)(
		'leads with Has tags and No tags on the %s wall, outside the five',
		async (subject) => {
			const values = [
				...Array.from({ length: 6 }, (_, at) => ({ value: `t${at}`, count: 20, label: `t${at}` })),
				{ value: 'none', count: 9 },
				{ value: 'any', count: 30 }
			];
			expect(await column(subject, 'tags', values)).toEqual([
				'Has tags',
				'No tags',
				't0',
				't1',
				't2',
				't3',
				't4'
			]);
		}
	);

	it('leaves Not enriched in its place, since only Tags leads with Has and No there', async () => {
		const values = [
			{ value: 'stashdb', count: 5 },
			{ value: 'none', count: 3 }
		];
		expect(await column('person', 'enriched', values)).toEqual([
			'Stash-box: StashDB',
			'Not enriched'
		]);
	});
});
