/*
 * Agreeing, field by field, to what a stash-box says about one of Sift's own subjects.
 *
 * The rule defended here cannot be seen by looking at the screen: a field Sift already has a value
 * for arrives untouched, with both values side by side, and is written only if somebody ticks it.
 * That is the whole conflict handling, deliberately not a queue: the person who pressed the button
 * is standing right there.
 *
 * The other two are about what a tick means. A list field is a merge, not a replacement, or taking
 * their other names would throw away the ones somebody typed; and applying writes the link first,
 * so a record never carries a stash-box's values with no record of which one said so.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { FieldDescription } from '$lib/entity/records.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({
	all: [] as FieldDescription[],
	search: vi.fn(),
	link: vi.fn(),
	linksOf: vi.fn(),
	refresh: vi.fn(),
	forgetLink: vi.fn(),
	post: vi.fn()
}));

/* The one request this sheet makes itself (the take) and the box list it reads to say which
   boxes were not asked. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn().mockResolvedValue({ boxes: [] }), post: mocks.post }
}));

/* A real registry with its one request replaced, rather than an object shaped like one. Which
   fields a record draws, and which sit behind the switch, are then answered by the class under
   test's own rules: an object retyping them here is a second copy that drifts, and a filter added
   to the registry would leave such a double answering that it did not exist. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(): FieldDescription[] {
			return mocks.all;
		}
	}
	return { ...real, fields: new Standing() };
});

vi.mock('$lib/entity/enrich.svelte', async (importActual) => ({
	// The pure reader stays REAL: what a found row's picture is, is the module's own rule, and a
	// stub of it would let the sheet draw a picture the module never chose.
	chooserPicture: (await importActual<typeof import('$lib/entity/enrich.svelte')>()).chooserPicture,
	search: mocks.search,
	link: mocks.link,
	linksOf: mocks.linksOf,
	refresh: mocks.refresh,
	forgetLink: mocks.forgetLink,
	problemFrom: () => 'That did not work.'
}));

import LinkToStashBox from './LinkToStashBox.svelte';

function field(over: Partial<FieldDescription> = {}): FieldDescription {
	return {
		key: 'birth_date',
		subject: 'person',
		label: 'Birthdate',
		kind: 'date',
		shown: 'record',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: null,
		ordered: false,
		...over
	};
}

function answer(fields: Record<string, unknown>): Record<string, unknown> {
	return {
		box_id: 'box-1',
		box_name: 'StashDB',
		fetched_at: 1,
		fresh: true,
		problem: null,
		records: [
			{
				source_id: 'box-1',
				source_name: 'StashDB',
				remote_id: 'remote-1',
				subject: 'person',
				name: 'Their Name',
				disambiguation: null,
				image_url: null,
				file_count: 3,
				fields,
				confidence: 0.5
			}
		]
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

beforeEach(() => {
	vi.clearAllMocks();
	mocks.all = [];
	mocks.linksOf.mockResolvedValue([]);
	mocks.link.mockResolvedValue({});
	mocks.post.mockResolvedValue({ fields: 0 });
});

/*
 * Every mount is kept, so `afterEach` can take it down. The sheet is portalled to `document.body`
 * rather than drawn inside the host this creates, so removing the host would leave the dialog in
 * the document, and a later test would find the previous test's "Take what is ticked", wired to the
 * previous callback.
 */
function draw(values: Record<string, unknown>): void {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(LinkToStashBox, {
		target: host,
		props: {
			open: true,
			subject: 'person' as const,
			id: 'person-1',
			name: 'Jane Doe',
			values
		}
	}) as Record<string, unknown>;
	flushSync();
}

/** The keys the one save sent to the take route, or null when nothing was sent. */
function takenKeys(): string[] | null {
	const call = mocks.post.mock.calls.find((one) => String(one[0]).endsWith('/take'));
	return call ? ((call[1] as { body: { keys: string[] } }).body.keys ?? []) : null;
}

/** Search, then pick the first candidate, which is where the confirm rows come from. */
async function pick(): Promise<void> {
	const look = [...document.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === 'Look up'
	);
	look?.click();
	await tick();
	await Promise.resolve();
	flushSync();
	await tick();
	const candidate = document.querySelector<HTMLButtonElement>('.found > li button');
	candidate?.click();
	flushSync();
	await tick();
}

/* Press "Take what is ticked", and let the whole chain behind it finish.
 *
 * Applying is two awaited calls in a row (the link, then the record) so one microtask is not
 * enough and a test that only turns the loop once reports a working save as a save that never
 * happened. Turned several times rather than a timer: there is nothing slow here, only a queue. */
async function keep(): Promise<void> {
	[...document.querySelectorAll('button')]
		.find((one) => one.textContent?.includes('Take what is ticked'))
		?.click();
	for (let turn = 0; turn < 8; turn += 1) {
		await Promise.resolve();
		flushSync();
	}
	await tick();
}

function rows(): { label: string; ticked: boolean; conflict: boolean }[] {
	return [...document.querySelectorAll('.rows > li')].map((li) => ({
		label: li.querySelector('button.box')?.getAttribute('aria-label') ?? '',
		ticked: li.querySelector('button.box')?.classList.contains('on') ?? false,
		conflict: li.classList.contains('conflict')
	}));
}

it('leaves a field Sift already holds UNTICKED, and ticks one it does not', async () => {
	mocks.all = [field(), field({ key: 'country', label: 'Nationality', kind: 'country' })];
	mocks.search.mockResolvedValue([answer({ birth_date: '1984-12-31', country: 'UA' })]);
	draw({ birth_date: '1991-07-09' });

	await pick();

	expect(rows()).toEqual([
		/* The label says what the tick DOES, and it differs by whether Sift already has an answer:
		   one replaces, the other fills a blank. "Keep Birthdate" against a column of dashes would
		   make the sheet look like it was about to keep the dashes. */
		{ label: 'Replace Birthdate with what StashDB says', ticked: false, conflict: true },
		{ label: 'Fill in Nationality from StashDB', ticked: true, conflict: false }
	]);
});

it('sends only the ticked KEYS to the take route, never the values, wherever it is opened', async () => {
	/*
	 * One way to save, from a record page and from the queue alike: the server takes each value
	 * from what was kept with the link, so the request names keys only. A value in the body would
	 * be a way to write anything into a record through a route that reads as an enrichment. The
	 * link still goes first.
	 */
	mocks.all = [field(), field({ key: 'country', label: 'Nationality', kind: 'country' })];
	mocks.search.mockResolvedValue([answer({ birth_date: '1984-12-31', country: 'UA' })]);
	draw({ birth_date: '1991-07-09' });
	await pick();

	await keep();

	expect(mocks.link).toHaveBeenCalledWith('person', 'person-1', 'box-1', 'remote-1');
	expect(mocks.post).toHaveBeenCalledTimes(1);
	expect(mocks.post).toHaveBeenCalledWith('/stash-boxes/links/person/person-1/box-1/take', {
		body: { keys: ['country'] }
	});
});

it('a list Sift already holds starts unticked, and ticking it sends the key to be merged', async () => {
	/* The merge itself is the writer's (`Writer.write` merges a list rather than replacing it):
	   what this sheet decides is only whether the key goes. */
	mocks.all = [field({ key: 'aliases', label: 'Aliases', kind: 'names' })];
	mocks.search.mockResolvedValue([answer({ aliases: ['jane doe', 'Janie'] })]);
	draw({ aliases: ['Jane Doe', 'JD'] });
	await pick();

	// Sift already holds two, so this row starts unticked. Ticking it is what a merge means.
	document.querySelector<HTMLButtonElement>('.rows > li button.box')?.click();
	flushSync();
	await keep();

	expect(takenKeys()).toEqual(['aliases']);
});

it('writes NOTHING to the subject when the link cannot be kept', async () => {
	/* The link goes first, and it is awaited.
	 *
	 * A record quietly carrying a stash-box's values with no record of which stash-box said so is
	 * the state worth never reaching, so a failure to keep the link has to stop the record write,
	 * and the sentence has to reach the screen rather than the console.
	 *
	 * Asserted as "the record was not written", not as "link was called first". Call ORDER survives
	 * dropping the await entirely, so a test written that way passes on code that fires both off
	 * and hopes, which is exactly the code this is meant to refuse.
	 */
	mocks.all = [field({ key: 'country', label: 'Nationality', kind: 'country' })];
	mocks.search.mockResolvedValue([answer({ country: 'UA' })]);
	mocks.link.mockRejectedValue(new Error('that box did not answer'));
	draw({});
	await pick();

	await keep();

	expect(takenKeys()).toBeNull();
	expect(document.querySelector('.stash-look')).not.toBeNull();
});

it('shows what is already linked, with a way to ask again and to forget', async () => {
	mocks.all = [field()];
	mocks.linksOf.mockResolvedValue([
		{
			box_id: 'box-1',
			box_name: 'StashDB',
			remote_id: 'remote-1',
			fetched_at: 1_600_000_000,
			record: { name: 'Their Name' }
		}
	]);
	draw({});
	await tick();
	await Promise.resolve();
	flushSync();

	const row = document.querySelector('.linked > li');
	expect(row?.textContent).toContain('StashDB');
	expect(row?.textContent).toContain('Their Name');
	const buttons = [...(row?.querySelectorAll('button') ?? [])].map((one) =>
		(one.querySelector('.label') ?? one).textContent?.trim()
	);
	expect(buttons).toEqual(['Ask again', 'Remove']);
});

it('ticks every row from one box, clears them all again, and reads half-ticked in between', async () => {
	/*
	 * The box above the rows: it says what the rows say, it makes the rows say something, and
	 * pressing it again takes it back. Three facts in one test because they are one behaviour;
	 * split, two could pass over a stuck control. The half-ticked reading is the one worth naming:
	 * a person whose birthdate Sift holds and whose nationality it does not starts with one row of
	 * two ticked, and a box drawing that as off would be offering to do something already half
	 * done.
	 */
	mocks.all = [field(), field({ key: 'country', label: 'Nationality', kind: 'country' })];
	mocks.search.mockResolvedValue([answer({ birth_date: '1984-12-31', country: 'UA' })]);
	draw({ birth_date: '1991-07-09' });
	await pick();

	const all = document.querySelector<HTMLButtonElement>('.take-all button.box');
	expect(all?.getAttribute('aria-label')).toBe('Take all from StashDB');
	expect(all?.className).toContain('partly');

	all?.click();
	flushSync();
	expect(rows().map((one) => one.ticked)).toEqual([true, true]);
	expect(document.querySelector('.take-all button.box')?.className).toContain('on');

	document.querySelector<HTMLButtonElement>('.take-all button.box')?.click();
	flushSync();
	expect(rows().map((one) => one.ticked)).toEqual([false, false]);
	const cleared = document.querySelector('.take-all button.box')?.className ?? '';
	expect(cleared).not.toContain('on');
	expect(cleared).not.toContain('partly');
});
