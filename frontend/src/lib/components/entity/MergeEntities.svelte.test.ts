/*
 * Two people, or two Sites, who turn out to be one.
 *
 * There is no undo, so the whole guard is before the press: the numbers are counted from the
 * database rather than estimated, and they are what somebody is agreeing to. These hold that the
 * count arrives with the decision rather than behind a second press, that the keeper is a real
 * choice and not whichever row came first, that it acts from a card's menu with nothing ticked,
 * that its consequences are lines rather than a paragraph, and that a merge that fails leaves the
 * thing there.
 *
 * The sheet is portalled, so every mount is taken down again: one left in the document would be
 * found first by the next test, wired to the last test's callback.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { Weighed } from '$lib/entity/merge.svelte';

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	weighMerge: vi.fn(),
	mergeInto: vi.fn()
}));

vi.mock('$lib/api/client', () => ({
	api: { get: mocks.get, post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

vi.mock('$lib/entity/merge.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/merge.svelte')>()),
	weighSeveral: mocks.weighMerge,
	mergeSeveral: mocks.mergeInto
}));

import MergeEntities from './MergeEntities.svelte';

function weighed(over: Partial<Weighed> = {}): Weighed {
	return {
		from_name: 'Jane',
		into_name: 'Jane Doe',
		files: 12,
		usernames: 0,
		aliases: 1,
		links: 0,
		faces: 0,
		children: 0,
		facts: 0,
		usernames_named: [],
		aliases_named: [],
		links_named: [],
		faces_from: [],
		children_named: [],
		filled: [],
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;
let merged: ReturnType<typeof vi.fn<(into: string) => void>>;

/** What the chooser's search answers, and whether it fails. */
let chooser: { items: { id: string; name: string }[]; total: number };
let chooserFails = false;
/** Every row the server holds BY ID: what the sheet reads each pick from. */
let held: Record<string, { id: string; name: string; asset_count: number }>;

/** Put rows on the server, so a pick of them can be read by id. */
function hold(rows: { id: string; name: string; files?: number }[]): void {
	for (const one of rows)
		held[one.id] = { id: one.id, name: one.name, asset_count: one.files ?? 0 };
}

beforeEach(() => {
	vi.clearAllMocks();
	chooser = {
		items: [
			{ id: 'p-2', name: 'Jane Doe' },
			{ id: 'p-3', name: 'Jane Roe' }
		],
		total: 2
	};
	chooserFails = false;
	held = {};
	hold([{ id: 'p-1', name: 'Jane' }]);
	/* A by-id read answers the row or refuses as the server would; anything else is the chooser. */
	mocks.get.mockImplementation(async (path: string) => {
		const byId = /^\/(?:people|sites)\/(.+)$/.exec(path);
		if (byId) {
			const row = held[byId[1] ?? ''];
			if (!row) throw new Error('404');
			return row;
		}
		if (chooserFails) throw new Error('offline');
		return chooser;
	});
	mocks.weighMerge.mockResolvedValue(weighed());
	mocks.mergeInto.mockResolvedValue(weighed());
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

/** The sheet as a person's own page or a card's menu opens it: one subject, no button of its own. */
function drawOne(kind: 'person' | 'site' = 'person', withButton = false): void {
	merged = vi.fn<(into: string) => void>();
	drawn = mount(MergeEntities, {
		target: host,
		props: {
			people: [{ id: 'p-1', name: 'Jane' }],
			kind,
			onmerged: merged,
			withButton,
			open: true
		}
	}) as Record<string, unknown>;
	flushSync();
}

/** The sheet as a WALL opens it: no button of its own, and already open on a selection. */
function drawSelection(people: { id: string; name: string; files?: number }[]): void {
	hold(people);
	merged = vi.fn<(into: string) => void>();
	drawn = mount(MergeEntities, {
		target: host,
		props: { ids: people.map((one) => one.id), onmerged: merged, withButton: false, open: true }
	}) as Record<string, unknown>;
	flushSync();
}

/** Let the debounce, the fetch and the count all land. */
async function settle(): Promise<void> {
	await new Promise((wake) => setTimeout(wake, 220));
	await tick();
	await tick();
	flushSync();
}

/** A button or a card anywhere on the page, including the portalled sheet.
 *
 * Matched on what it CONTAINS rather than on its whole text: a control with an icon carries the
 * icon's ligature as text of its own, so an exact comparison finds nothing and says the control is
 * missing. */
function press(text: string): void {
	const found = [...document.querySelectorAll('button')].find((one) =>
		(one.textContent ?? '').includes(text)
	);
	if (!found) throw new Error(`no button reading "${text}"`);
	found.click();
	flushSync();
}

/** The act at the foot of the sheet, whatever it currently says. */
function act(): HTMLButtonElement {
	const found = [...document.querySelectorAll('button')].find((one) =>
		(one.textContent ?? '').trim().startsWith('Merge')
	);
	if (!found) throw new Error('no merge button');
	return found as HTMLButtonElement;
}

/** Every line of the summary, in the order they are drawn. */
function lines(): string[] {
	return [...document.querySelectorAll('li')].map((one) => (one.textContent ?? '').trim());
}

it('offers the whole library and counts as soon as one is pressed, with no second press', async () => {
	/*
	 * From a card's menu the sheet must act: pressing a candidate is the answer, and what it would
	 * move arrives under it, rather than a finishing button disabled until something is ticked.
	 */
	drawOne();
	await settle();

	expect(document.body.textContent).toContain('Jane Doe');
	expect(mocks.weighMerge).not.toHaveBeenCalled();

	press('Jane Doe');
	await settle();

	expect(mocks.weighMerge).toHaveBeenCalledWith('person', ['p-1'], 'p-2');
	expect(mocks.mergeInto).not.toHaveBeenCalled();
});

it('never offers to merge somebody into themselves', async () => {
	chooser = {
		items: [
			{ id: 'p-1', name: 'Jane' },
			{ id: 'p-2', name: 'Jane Doe' }
		],
		total: 2
	};
	drawOne();
	await settle();

	const offered = [...document.querySelectorAll('button')].map((one) => one.textContent ?? '');
	expect(offered.filter((one) => one.includes('Jane Doe'))).not.toHaveLength(0);
	expect(
		offered.filter((one) => /(^|\W)Jane(\W|$)/.test(one) && !one.includes('Jane Doe'))
	).toEqual([]);
});

it('says what it is waiting for instead of leaving a dead button on the sheet', async () => {
	/* The failure this is for is not a wrong sentence, it is a screen that appears to do nothing:
	   a control that cannot be pressed with nothing anywhere saying why. */
	drawOne();
	await settle();

	expect(act().disabled).toBe(true);
	expect(document.body.textContent).toContain('Pick the person to keep');
});

it('says so when the library could not be read, rather than drawing an empty chooser', async () => {
	chooserFails = true;
	drawOne();
	await settle();

	expect(document.body.textContent).toContain("That didn't work.");
});

it('asks the server for what was typed, so anybody past the first page can be merged into', async () => {
	/*
	 * The filtering is the server's, like every other picker's, so the chooser reaches the whole
	 * library.
	 */
	drawOne();
	await settle();

	const box = document.querySelector('input');
	if (!box) throw new Error('no search box');
	box.value = 'Jane Roe';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await settle();

	expect(mocks.get).toHaveBeenCalledWith(
		'/people',
		expect.objectContaining({ query: expect.objectContaining({ prefix: 'Jane Roe' }) })
	);
});

it('folds the list away once somebody is picked, so what would happen is on the screen', async () => {
	/* Sixty candidates left open would put the summary a screen below the choice, out of sight
	   when the choice is made. The way back is a press beside the row. */
	drawOne();
	await settle();
	press('Jane Doe');
	await settle();

	expect(document.querySelector('input')).toBeNull();
	expect(document.body.textContent).toContain('12 files move to Jane Doe');
	expect(document.body.textContent).not.toContain('Jane Roe');

	press('Pick somebody else');
	await settle();
	expect(document.querySelector('input')).not.toBeNull();
});

it('keeps the one that was pressed when the search moves on under it', async () => {
	/* Looked up in the answer rather than held, a choice VANISHES on the next keystroke: the row is
	   not on the new page, the sheet has no keeper again, and the press reads as forgotten. */
	drawOne();
	await settle();
	press('Jane Doe');
	await settle();
	press('Pick somebody else');
	await settle();

	chooser = { items: [{ id: 'p-9', name: 'Jane Somebody' }], total: 1 };
	const box = document.querySelector('input');
	if (!box) throw new Error('no search box');
	box.value = 'Some';
	box.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	await settle();

	expect(act().textContent).toContain('Merge into Jane Doe');
	expect(act().disabled).toBe(false);
});

it('weighs a SITE against the sites, not against the people', async () => {
	/* The one thing one sheet serving two subjects can get wrong, and it is silent: the numbers
	   would come back for the wrong kind of thing, or the chooser would offer people to somebody
	   merging two sites. Both look like a working screen. */
	drawOne('site');
	await settle();
	press('Jane Doe');
	await settle();

	expect(mocks.get).toHaveBeenCalledWith('/sites', expect.anything());
	expect(mocks.weighMerge).toHaveBeenCalledWith('site', ['p-1'], 'p-2');
});

it('draws a Site with no picture by the Sites glyph, never by a letter', async () => {
	/* `siteRow` answers no picture for a Site the shipped pack does not cover and nothing was chosen
	   for. `Avatar` drawn over that empty answer would fall to the name's first letter: the look
	   of a PERSON with no face, on a Site. */
	chooser = {
		items: [
			{ id: 'p-2', name: 'Covered', icon: 'pack-token' } as { id: string; name: string },
			{ id: 'p-3', name: 'Uncovered' }
		],
		total: 2
	};
	drawOne('site');
	await settle();

	const card = (name: string) =>
		[...document.querySelectorAll('button, [role="radio"]')].find((one) =>
			(one.textContent ?? '').includes(name)
		);
	expect(card('Uncovered')?.querySelector('[data-glyph="site"]')).not.toBeNull();
	expect(card('Covered')?.querySelector('[data-glyph="site"]')).toBeNull();

	press('Uncovered');
	await settle();
	expect(document.body.textContent).toContain('Pick another Site');
	expect(document.body.textContent).not.toContain('Pick somebody else');
});

it('still draws a person with no face by the letter', async () => {
	drawOne('person');
	await settle();

	expect(document.querySelector('[data-glyph="site"]')).toBeNull();
});

it('opens on the one holding the most files and says that is why', async () => {
	/*
	 * The default is the whole of what a selection asks for, and a default nobody can see the
	 * reason for reads as the machine having decided: on a wall sorted largest first, merging into
	 * the biggest looks like "always the first person listed on the page" unless the screen says
	 * otherwise.
	 */
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 },
		{ id: 'p-3', name: 'Jane Roe', files: 3 }
	]);
	await settle();

	const chosen = document.querySelector('[aria-checked="true"]');
	expect(chosen?.textContent).toContain('Jane Doe');
	expect(chosen?.textContent).toContain('most files');
	// Every one of them is offered, with what it holds, so the choice can be made by looking.
	expect(document.body.textContent).toContain('12 files');
	expect(document.body.textContent).toContain('3 files');
	expect(mocks.weighMerge).toHaveBeenCalledWith('person', ['p-1', 'p-3'], 'p-2');
});

it('counts again against the new keeper when the choice is moved', async () => {
	/* The question somebody has in front of two candidates is which way round it should go, and the
	   only thing that answers it is seeing both sets of numbers. A press between the choice and the
	   count makes comparing them a matter of memory. */
	/* Two names that are not prefixes of each other, so pressing one cannot be a press on the
	   other: the cards carry the whole name and `press` matches on what a control CONTAINS. */
	drawSelection([
		{ id: 'p-1', name: 'Jane Roe', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	press('Jane Roe');
	await settle();

	expect(mocks.weighMerge).toHaveBeenLastCalledWith('person', ['p-2'], 'p-1');
	expect(act().textContent).toContain('Merge into Jane Roe');
});

it('groups every count it says the way every other count on screen is grouped', async () => {
	// Ungrouped, "8395 files move to" would sit beside a filter panel's grouped figures: the
	// server's counts reach the lines raw.
	mocks.weighMerge.mockResolvedValue(
		weighed({ files: 8395, aliases: 0, usernames: 1250, faces: 1024 })
	);
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	const said = lines();
	expect(said[0]).toBe(`${(8395).toLocaleString()} files move to Jane Doe`);
	expect(said.some((one) => one.startsWith(`${(1250).toLocaleString()} usernames`))).toBe(true);
	expect(said.some((one) => one.startsWith(`${(1024).toLocaleString()} confirmed faces`))).toBe(
		true
	);
});

it('says what moves in lines, files first, leaving out the kinds there are none of', async () => {
	/*
	 * Short lines, files first: the number that decides the press must not be a clause buried in a
	 * paragraph.
	 */
	mocks.weighMerge.mockResolvedValue(weighed({ files: 12, aliases: 1, usernames: 0, faces: 0 }));
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	const said = lines();
	expect(said[0]).toBe('12 files move to Jane Doe');
	expect(said).toContain('1 alias moves to Jane Doe');
	expect(said.some((one) => one.startsWith('0 '))).toBe(false);
	// The words the screen uses, never the database's: "handle" and "fact" are terms only somebody
	// who has read the schema can price, on a press that cannot be undone.
	expect(document.body.textContent).not.toContain('handle');
});

it('always promises what does not happen, whatever the counts are', async () => {
	// The reassurances are what make the press safe to make, and one that appears only sometimes is
	// one somebody has to go looking for.
	mocks.weighMerge.mockResolvedValue(
		weighed({ files: 0, usernames: 0, aliases: 0, links: 0, faces: 0, children: 0, facts: 0 })
	);
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 0 },
		{ id: 'p-2', name: 'Jane Doe', files: 0 }
	]);
	await settle();

	const said = lines();
	expect(said).toContain('Nothing already filled in changes');
	expect(said).toContain('No file is deleted');
	expect(said).toContain("This can't be undone");
	expect(said.some((one) => one.includes('searchable'))).toBe(true);
});

it('counts the sites published under one, which move rather than disappear', async () => {
	// A label whose network is folded into another keeps existing, so the line must not say it is
	// going.
	mocks.weighMerge.mockResolvedValue(weighed({ files: 0, aliases: 0, children: 3 }));
	drawOne('site');
	await settle();
	press('Jane Doe');
	await settle();

	expect(document.body.textContent).toContain('3 Sites published under');
	expect(document.body.textContent).toContain('none of them is removed');
});

it('names the one that is going when there is one of it, and never says "1 people"', async () => {
	/*
	 * Two selected are named, not "Fold 1 people into Jane Doe?" with "the other 1" in the
	 * paragraph under it. Two or more going are counted, because four names in a line is worse than
	 * the number.
	 */
	drawSelection([
		{ id: 'p-1', name: 'Jane Roe', files: 4 },
		{ id: 'p-2', name: 'Jane Doe', files: 40 }
	]);
	await settle();

	expect(lines()).toContain('Jane Roe is then removed, and the name stays searchable');
	expect(document.body.textContent).not.toContain('1 people');
	expect(document.body.textContent).not.toContain('the other 1');
});

it('says which way it goes on the button that does it', async () => {
	/* The one control on the sheet that must not be generic: it is the last thing read before
	   something that cannot be taken back. */
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	expect(act().textContent).toContain('Merge into Jane Doe');

	act().click();
	await tick();
	await tick();
	flushSync();

	expect(mocks.mergeInto).toHaveBeenCalledWith('person', ['p-1'], 'p-2');
	expect(merged).toHaveBeenCalledWith('p-2');
});

it('leaves the thing where it is when the merge is refused, and says so on the sheet', async () => {
	/*
	 * The refusal must reach somebody on every surface. The sheet stays open on a refusal (two
	 * Sites each with cookies saved, only one of which can be kept), so there is always somewhere
	 * to say it.
	 */
	mocks.mergeInto.mockRejectedValue(new Error('no'));
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	act().click();
	await tick();
	await tick();
	flushSync();

	expect(merged).not.toHaveBeenCalled();
	expect(document.body.textContent).toContain("That didn't work.");
});

it('does not throw away the keeper, or ask again, when the wall behind it redraws', async () => {
	/* Two of the four callers build `people` with a `.map` in their template, so the prop is a fresh
	   array on every redraw of the wall behind the sheet. Tracked, the sheet would reset the choice
	   somebody had just made and start another count on each of them. */
	hold([
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	merged = vi.fn<(into: string) => void>();
	const props = $state({
		people: [
			{ id: 'p-1', name: 'Jane', files: 12 },
			{ id: 'p-2', name: 'Jane Doe', files: 900 }
		],
		onmerged: merged,
		withButton: false,
		open: true
	});
	drawn = mount(MergeEntities, { target: host, props }) as Record<string, unknown>;
	flushSync();
	await settle();

	press('Jane');
	await settle();
	const counted = mocks.weighMerge.mock.calls.length;

	// The same rows, rebuilt: exactly what a wall's `.map` hands over on a redraw.
	props.people = [
		{ id: 'p-1', name: 'Jane', files: 12 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	];
	await settle();

	expect(act().textContent).toContain('Merge into Jane');
	expect(mocks.weighMerge.mock.calls.length).toBe(counted);
});

it('reads every pick by id, so one that was never on the loaded page still reaches the sheet', async () => {
	/*
	 * Picks found by a search must not fall out. The wall hands ids, and the sheet reads both
	 * people from the server and keeps the bigger, so picking her first or last merges the right
	 * way round.
	 */
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 3 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	expect(document.body.textContent).toContain('Merge 2 people');
	expect(document.body.textContent).not.toContain('into somebody');
	expect(mocks.get).toHaveBeenCalledWith('/people/p-1');
	expect(mocks.get).toHaveBeenCalledWith('/people/p-2');
	expect(mocks.weighMerge).toHaveBeenCalledWith('person', ['p-1'], 'p-2');
	expect(act().textContent).toContain('Merge into Jane Doe');
});

it('refuses, in words, when a pick cannot be read, rather than merging the ones that could', async () => {
	// Merging the rest would be a silent shrink of what was picked.
	hold([{ id: 'p-2', name: 'Jane Doe', files: 900 }]);
	merged = vi.fn<(into: string) => void>();
	drawn = mount(MergeEntities, {
		target: host,
		props: { ids: ['p-2', 'p-gone'], onmerged: merged, withButton: false, open: true }
	}) as Record<string, unknown>;
	flushSync();
	await settle();

	expect(document.body.textContent).toContain("One of the people picked couldn't be read");
	expect(act().disabled).toBe(true);
	expect(mocks.weighMerge).not.toHaveBeenCalled();
});

it('says WHICH username, WHAT field and which aliases, each in its own line', async () => {
	/* Counts alone are not enough: each line names the username and the field it is about. */
	mocks.weighMerge.mockResolvedValue(
		weighed({
			files: 2,
			usernames: 1,
			usernames_named: [{ name: 'janedoe', where: 'Another Studio', whose: 'Jane' }],
			aliases: 2,
			aliases_named: [
				{ name: 'JD', where: null, whose: 'Jane' },
				{ name: 'J. Doe', where: null, whose: 'Jane' }
			],
			faces: 3,
			faces_from: [{ whose: 'Jane', count: 3 }],
			facts: 1,
			filled: [{ key: 'birth_date', label: 'Birthdate', value: '1998-04-02', whose: 'Jane' }]
		})
	);
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 2 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	const said = lines();
	expect(said[0]).toBe('2 files move to Jane Doe');
	expect(said).toContain('Username janedoe on Another Studio moves too');
	expect(said).toContain('2 aliases move to Jane Doe: JD, J. Doe');
	expect(said).toContain('3 confirmed faces move too, from Jane');
	expect(said).toContain('Birthdate, missing here, is filled in from Jane: 1998-04-02');
	expect(said).toContain('Nothing already filled in changes');
});

it('folds a long list past five, opens it in place, and says how many it was never told', async () => {
	/* Seven named of nine: the line shows five, and "Show 4 more" opens the other two named in
	   place, leaving "and 2 more" for the ones the server counted and did not name. The press is
	   a control, so it opens with its verb; the words left behind are a sentence and stay "and". */
	const named = ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7'].map((name) => ({
		name,
		where: 'Another Studio',
		whose: 'Jane'
	}));
	mocks.weighMerge.mockResolvedValue(weighed({ usernames: 9, usernames_named: named }));
	drawSelection([
		{ id: 'p-1', name: 'Jane', files: 2 },
		{ id: 'p-2', name: 'Jane Doe', files: 900 }
	]);
	await settle();

	const folded = lines().find((one) => one.startsWith('9 usernames'));
	expect(folded).toContain('a5 on Another Studio');
	expect(folded).not.toContain('a6');
	expect(folded).toContain('Show 4 more');

	press('Show 4 more');
	await tick();
	const opened = lines().find((one) => one.startsWith('9 usernames'));
	expect(opened).toContain('a7 on Another Studio');
	expect(opened).toContain('and 2 more');
});
